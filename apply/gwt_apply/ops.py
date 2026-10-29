"""Validate a change list and apply it, one op at a time, reading before every write."""

from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .client import ApiError, Client, Retryable
from .dates import DateError, date_key, parse_date

TARGET_KINDS = {
    "person": "Person",
    "family": "Family",
    "event": "Event",
    "citation": "Citation",
    "source": "Source",
    "media": "Media",
    "place": "Place",
    "repository": "Repository",
}

# op -> (required fields, optional fields), on top of op/key/ruling
SPECS: dict[str, tuple[set[str], set[str]]] = {
    "add_person": (
        {"surname", "first_name"},
        {"gender", "gramps_id", "allow_namesake", "namesake_reason", "namesake_of"},
    ),
    "update_person": ({"person", "set"}, set()),
    "update_event": ({"event", "set"}, set()),
    "update_place": ({"place", "set"}, set()),
    "update_note": ({"note", "set"}, {"expect"}),
    "update_citation": ({"citation", "set"}, {"expect"}),
    "update_family": ({"family", "set"}, set()),
    "update_child_ref": ({"family", "child"}, {"set", "confirm_probable", "expect"}),
    "detach": ({"target", "object"}, set()),
    "add_event": ({"type"}, {"person", "family", "role", "date", "place", "description", "gramps_id"}),
    "add_family": (set(), {"father", "mother", "children", "type", "gramps_id", "allow_new_couple", "new_couple_reason",
                           "new_couple_of"}),
    "link_child": ({"family", "child"}, {"frel", "mrel", "probable", "probable_reason"}),
    "unlink_child": ({"family", "child", "reason"}, {"drop_link_sources"}),
    "add_place": ({"name", "type"}, {"enclosed_by", "lat", "long", "gramps_id"}),
    "add_source": ({"title"}, {"author", "pubinfo", "abbrev", "gramps_id"}),
    "add_repository": ({"name", "type"}, {"gramps_id", "url", "address"}),
    "update_repository": ({"repository", "set"}, set()),
    "link_repository": ({"source", "repository"}, {"call_number", "media_type"}),
    "unlink_repository": ({"source", "repository"}, set()),
    "merge_repository": ({"winner", "loser"}, {"reason"}),
    "add_citation": ({"source", "page", "attach"}, {"confidence", "date", "gramps_id"}),
    "add_media": ({"file", "path", "attach"}, {"desc", "mime", "gramps_id"}),
    "add_note": ({"text", "attach"}, {"type", "gramps_id"}),
    "add_transcription": ({"text", "margin_reading", "attach"}, {"gramps_id"}),
    "attach_existing": ({"object", "attach"}, set()),
    "add_tag": ({"target", "name"}, set()),
    "remove_tag": ({"target", "name"}, set()),
    "move_event": ({"event", "from", "to"}, {"role"}),
    "dedupe_list": ({"target", "list"}, set()),
    "set_attribute": ({"target", "type", "value"}, set()),
    "merge_person": ({"winner", "loser"}, {"use_loser", "reason"}),
    "merge_family": ({"winner", "loser"}, {"reason"}),
    "merge_event": ({"winner", "loser"}, {"reason"}),
    "add_association": ({"person", "target", "relation"}, set()),
}
MERGE_LISTS = [  # what Gramps' merge carries from the loser to the winner, list by list
    "alternate_names", "event_ref_list", "citation_list", "note_list", "media_list", "attribute_list",
    "address_list", "urls", "person_ref_list", "lds_ord_list", "tag_list", "parent_family_list", "family_list",
    "child_ref_list",
]
USE_LOSER = {"birth": "birth_ref_index", "death": "death_ref_index"}
ATTACHABLE = {"citation": ("Citation", "citation_list"), "media": ("Media", "media_list"),  # attach_existing
              "note": ("Note", "note_list")}
REF_OWN_LISTS = ("note_list", "citation_list", "attribute_list")  # what a MediaRef carries besides its ref
# fields that are not one line of text; every other field in SPECS is a non-blank string. A field added to SPECS
# gets that text check by default: list it here if it is not text, or in TEXT_CHECKED_BELOW if validate() checks it
# by hand further down.
NOT_TEXT = {"set", "target", "object", "attach", "children", "namesake_of", "use_loser", "address",
            "allow_namesake", "new_couple_of", "allow_new_couple", "probable", "confidence", "lat", "long", "expect",
            "drop_link_sources", "from", "to"}
TEXT_CHECKED_BELOW = {("add_repository", "name"), ("add_repository", "type"), ("link_repository", "media_type"),
                      ("add_association", "relation"), ("add_transcription", "margin_reading"),
                      ("update_child_ref", "confirm_probable"), ("add_person", "gender"),
                      ("dedupe_list", "list")}
PERSON_FIELDS = {"first_name", "surname", "gender"}
ADDRESS_KEYS = {"street", "locality", "city", "county", "state", "country", "postal", "phone"}
REPOSITORY_FIELDS = {"name", "type", "url", "address"}
EVENT_FIELDS = {"type", "date", "place", "description"}
PLACE_FIELDS = {"lat", "long", "enclosed_by", "name"}
NOTE_FIELDS = {"text", "type"}
CITATION_FIELDS = {"page", "confidence", "date"}
FAMILY_FIELDS = {"type"}
CHILD_REF_FIELDS = {"frel", "mrel"}
CONFIRMED = "[confirmed]"  # what a [probable] mark becomes once a document settles the link
GENDER = {"F": 0, "M": 1, "U": 2}
SINGLE_VITAL = {"Birth": "birth_ref_index", "Death": "death_ref_index"}
PROBABLE = "[probable]"  # how a doubtful parent link is marked: a note on its child_ref starting with this
WRONG_LINK = "[wrong link]"  # how a parent link pending removal is marked, the same way
LINK_MARKS = (PROBABLE, CONFIRMED, WRONG_LINK)  # marks that only describe the link: unlink_child removes them with it
# what each target kind can carry
CARRIES = {
    "citation_list": {"Person", "Family", "Event", "Media", "Place"},
    "media_list": {"Person", "Family", "Event", "Citation", "Source", "Place"},
    "note_list": {"Person", "Family", "Event", "Citation", "Source", "Media", "Place", "Repository", "ChildRef"},
}
CARRIES["citation_list"] = CARRIES["citation_list"] | {"ChildRef", "PersonRef"}
CARRIES["note_list"] = CARRIES["note_list"] | {"PersonRef"}


def fold(text: str) -> str:
    """Compare names without case, accents or extra spaces: "Agüera" == "aguera"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(bare.casefold().split())


_GRAMPS_ID = re.compile(r"^[A-Za-z]+\d+(?:-[A-Za-z0-9]+)?$")  # I0042, or I0042-b: an import may add a suffix


def _event_fact(event: dict, field: str):
    """What merge_event compares: the whole date (modifier, quality, values, and the text of a
    text-only date), the place handle, the description. None when the event has none."""
    value = event.get(field)
    if field != "date":
        return value or None
    key, text = date_key(value), (value or {}).get("text") or ""
    if key[2] == (0, 0, 0):
        return (key, text) if text else None
    return key


def _date_text(date: dict | None) -> str:
    """A stored date as a short text for a message: 1890-03-16, 1890, or its text; "no date" when empty."""
    key = date_key(date)
    if key[2] == (0, 0, 0):
        return (date or {}).get("text") or "no date"
    day, month, year = key[2][:3]
    text = "-".join([f"{year:04d}", *([f"{month:02d}"] if month else []), *([f"{day:02d}"] if month and day else [])])
    return text + (" (with a modifier or quality)" if key[0] or key[1] else "")


class Conflict(Exception):
    """The tree disagrees with the op in a way only a person can settle."""


def stable_handle(key: str) -> str:
    """The handle a key's object gets: same key, same handle, on every run."""
    return "gwt" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:25]


def load_change_list(path: str) -> list[dict]:
    """A JSON array, an object {"ops": [...]}, or JSONL (one op per line)."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict):
        return data["ops"] if "ops" in data else [data]
    return data


def _parse_target(spec) -> tuple[str, object]:
    """{"person": "I1"} -> ("Person", "I1"); {"child_ref": {"family": F, "child": I}} -> ("ChildRef", (F, I));
    {"association": {"person": P, "target": T[, "relation": R]}} -> ("PersonRef", (P, T, R or None))."""
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"a target is one of {sorted(TARGET_KINDS) + ['association', 'child_ref']} -> ref, got {spec!r}")
    (kind, ref), = spec.items()
    if kind == "association":
        if not isinstance(ref, dict) or not {"person", "target"} <= set(ref) <= {"person", "target", "relation"}:
            raise ValueError(f"an association target is {{\"person\": ..., \"target\": ...[, \"relation\": ...]}}, got {ref!r}")
        return "PersonRef", (ref["person"], ref["target"], ref.get("relation"))
    if kind == "child_ref":
        if not isinstance(ref, dict) or set(ref) != {"family", "child"}:
            raise ValueError(f"a child_ref target is {{\"family\": ..., \"child\": ...}}, got {ref!r}")
        return "ChildRef", (ref["family"], ref["child"])
    if kind not in TARGET_KINDS or not isinstance(ref, str) or not ref:
        raise ValueError(f"bad target {spec!r}")
    return TARGET_KINDS[kind], ref


def _tag_target(spec) -> tuple[str, str]:
    """add_tag's target: any target kind or a note ({"note": "N0001"}); a child_ref or an association has no tags."""
    if isinstance(spec, dict) and len(spec) == 1 and "note" in spec:
        if not isinstance(spec["note"], str) or not spec["note"]:
            raise ValueError(f"bad target {spec!r}")
        return "Note", spec["note"]
    cls, ref = _parse_target(spec)
    if cls in ("ChildRef", "PersonRef"):
        raise ValueError("a child_ref or an association has no tags")
    return cls, ref


def _event_owner(spec) -> tuple[str, str]:
    """move_event's from and to: {"person": "I0001"} or {"family": "F0001"}, the two kinds that list events."""
    cls, ref = _parse_target(spec)
    if cls not in ("Person", "Family"):
        raise ValueError(f"an event belongs to a person or a family, got {spec!r}")
    return cls, ref


def _parse_object(spec) -> tuple[str, str, str]:
    """attach_existing's object: {"media": "O0054"} -> ("Media", "O0054", "media_list")."""
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"object is one of {sorted(ATTACHABLE)} -> ref, got {spec!r}")
    (kind, ref), = spec.items()
    if kind not in ATTACHABLE or not isinstance(ref, str) or not ref:
        raise ValueError(f"object is a citation, media or note, got {kind}")
    cls, lst = ATTACHABLE[kind]
    return cls, ref, lst


def _target_refs(spec) -> list:
    if not isinstance(spec, dict) or len(spec) != 1:
        return []
    value = next(iter(spec.values()))
    return [v for k, v in value.items() if k != "relation"] if isinstance(value, dict) else [value]


def _tag_names(value) -> list:
    """A per-op `tag`: one name or a list of names."""
    return value if isinstance(value, list) else [value]


def _ref_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _check_coords(f: dict) -> None:
    """lat and long of add_place and update_place: both or neither, numbers, in range."""
    if ("lat" in f) != ("long" in f):
        raise ValueError("lat and long go together")
    if "lat" not in f:
        return
    try:
        lat, long = float(f["lat"]), float(f["long"])
    except (TypeError, ValueError):
        raise ValueError("lat and long are numbers") from None
    if not (-90 <= lat <= 90 and -180 <= long <= 180):
        raise ValueError("lat/long out of range")


def validate(ops: list[dict]) -> list[str]:
    """Every problem with the change list, found before anything is read or written."""
    errors = []
    keys: set[str] = set()
    payloads: dict[str, str] = {}  # the op without key and ruling -> where it was first seen
    pages: dict[tuple, tuple[str, dict]] = {}  # (source, page) of an add_citation -> (where, the op that makes it)
    for i, op in enumerate(ops, 1):
        where = f"op {i}"
        if not isinstance(op, dict):
            errors.append(f"{where}: not an object")
            continue
        name = op.get("op")
        if name not in SPECS:
            errors.append(f"{where}: unknown op {name!r}")
            continue
        where = f"op {i} ({name} {op.get('key')!r})"
        if not str(op.get("ruling") or "").strip():
            errors.append(f"{where}: no ruling; every change must name the decision it comes from")
        key = op.get("key")
        if not isinstance(key, str) or not key.strip():
            errors.append(f"{where}: no key")
        elif key in keys:
            errors.append(f"{where}: key used twice")
        else:
            keys.add(key)
        required, optional = SPECS[name]
        allowed = required | optional | {"op", "key", "ruling", "tag"}
        if "tag" in op and not (_tag_names(op["tag"]) and all(isinstance(t, str) and t.strip() for t in _tag_names(op["tag"]))):
            errors.append(f"{where}: tag is a non-empty name, or a list of them")
        missing = [f for f in required if op.get(f) in (None, "", [])]
        if name == "add_citation" and op.get("page") == "":
            missing.remove("page")  # an empty page is a real value
        if name == "add_person" and op.get("surname") == "":
            missing.remove("surname")  # someone known by one name only has no surname
        if missing:
            errors.append(f"{where}: missing {sorted(missing)}")
        extra = set(op) - allowed
        if extra:
            errors.append(f"{where}: unknown fields {sorted(extra)}")
        for f in sorted((required | optional) - NOT_TEXT):
            if f not in op or f in missing or (name, f) in TEXT_CHECKED_BELOW:
                continue
            if f in optional and op[f] in (None, ""):
                continue  # an optional text left "" or null means none, as older lists write it
            blank_ok = (name, f) in (("add_citation", "page"), ("add_person", "surname"))
            if not isinstance(op[f], str) or not (op[f].strip() or (blank_ok and op[f] == "")):
                errors.append(f"{where}: {f} is a non-empty text")
        for f in ("children", "attach", "namesake_of", "new_couple_of", "use_loser"):
            if f in op and not isinstance(op[f], list) and not (f.endswith("_of") and isinstance(op[f], str)):
                errors.append(f"{where}: {f} is a list of " + ("targets" if f == "attach" else "names" if
                                                                 f == "use_loser" else "Gramps IDs or key:<key>"))
                op = {k: v for k, v in op.items() if k != f}  # checked no further
        if name == "add_citation" and "confidence" in op and (
                isinstance(op["confidence"], bool) or op["confidence"] not in (0, 1, 2, 3, 4)):
            errors.append(f"{where}: confidence is a whole number 0-4")
        if name == "add_citation" and isinstance(op.get("source"), str) and isinstance(op.get("page"), str):
            first = pages.setdefault((op["source"], op["page"].strip()), (where, op))
            if first[1] is not op:
                errors += [f"{where}: {first[0]} cites the same page with {f} {theirs}; this op says {ours}. "
                           "One page text per role: give each its own page, e.g. add the role to it"
                           for f, theirs, ours in _citation_differs(first[1], op)]
        payload = json.dumps({k: v for k, v in op.items() if k not in ("key", "ruling")}, sort_keys=True)
        if payload in payloads:
            errors.append(f"{where}: the same change as {payloads[payload]}; one change, one op")
        else:
            payloads[payload] = where
        refs = [op.get(f) for f in ("person", "family", "father", "mother", "child", "source", "place", "enclosed_by",
                                    "winner", "loser", "repository", "note", "citation")]
        refs += list(op.get("children") or [])
        if name == "attach_existing":
            refs += _target_refs(op.get("object"))
        if name == "add_association":
            refs.append(op.get("target"))
        refs += _ref_list(op.get("namesake_of")) if op.get("namesake_of") else []
        refs += _ref_list(op.get("new_couple_of")) if op.get("new_couple_of") else []
        for t in [op.get("target"), *(op.get("attach") or [])]:
            refs += _target_refs(t)
        if name == "detach":
            refs += _target_refs(op.get("object"))
        if name == "move_event":
            refs += [op.get("event"), *_target_refs(op.get("from")), *_target_refs(op.get("to"))]
        for ref in refs:
            if ref is not None and not (isinstance(ref, str) and (ref.startswith("key:") or _GRAMPS_ID.match(ref))):
                errors.append(f"{where}: {ref!r} is neither key:<key> nor a Gramps ID (never a handle)")
        try:
            if "date" in op:
                parse_date(op["date"])
            if "gender" in op and (not isinstance(op["gender"], str) or op["gender"] not in GENDER):
                raise ValueError(f"gender is one of {sorted(GENDER)}")
            if name == "add_person" and op.get("allow_namesake"):
                if op["allow_namesake"] is not True:
                    raise ValueError("allow_namesake is true or absent")
                if not str(op.get("namesake_reason") or "").strip() or not op.get("namesake_of"):
                    raise ValueError(
                        "allow_namesake needs namesake_reason (why this is another person) and "
                        "namesake_of (the Gramps ID or key:<key> of each namesake you compared)"
                    )
            elif name == "add_person" and ("namesake_reason" in op or "namesake_of" in op):
                raise ValueError("namesake_reason and namesake_of go with allow_namesake: true")
            if name == "add_family" and op.get("allow_new_couple"):
                if op["allow_new_couple"] is not True:
                    raise ValueError("allow_new_couple is true or absent")
                if not (op.get("father") and op.get("mother")):
                    raise ValueError("allow_new_couple goes on a family with both a father and a mother")
                if not str(op.get("new_couple_reason") or "").strip() or not op.get("new_couple_of"):
                    raise ValueError(
                        "allow_new_couple needs new_couple_reason (why this is another couple) and "
                        "new_couple_of (the Gramps ID or key:<key> of each family you compared)"
                    )
            elif name == "add_family" and ("new_couple_reason" in op or "new_couple_of" in op):
                raise ValueError("new_couple_reason and new_couple_of go with allow_new_couple: true")
            if name == "link_child":
                if op.get("probable", True) is not True:
                    raise ValueError("probable is true or absent")
                if bool(op.get("probable")) != bool(str(op.get("probable_reason") or "").strip()):
                    raise ValueError("probable: true and probable_reason go together")
            if name == "unlink_child" and "drop_link_sources" in op and not isinstance(op["drop_link_sources"], bool):
                raise ValueError("drop_link_sources is true or false")
            if name == "add_association":
                if op.get("person") == op.get("target"):
                    raise ValueError("person and target are the same")
                if not isinstance(op.get("relation"), str) or not op["relation"].strip():
                    raise ValueError("relation is a non-empty text, e.g. DNA")
            if name.startswith("merge_") and op.get("winner") == op.get("loser"):
                raise ValueError("winner and loser are the same")
            if name == "merge_person" and set(op.get("use_loser") or []) - set(USE_LOSER):
                raise ValueError(f"use_loser takes {sorted(USE_LOSER)}")
            if name == "add_transcription" and not str(op.get("margin_reading") or "").strip():
                raise ValueError("margin_reading is required: what the margins say, or 'none' once you looked")
            if name == "dedupe_list":
                cls, _ = _parse_target(op.get("target"))
                if not isinstance(op.get("list"), str) or op["list"] not in CARRIES:
                    raise ValueError(f"list is one of {sorted(CARRIES)}")
                if cls not in CARRIES[op["list"]]:
                    raise ValueError(f"a {cls} has no {op['list']}")
            if name == "add_event" and bool(op.get("person")) == bool(op.get("family")):
                raise ValueError("add_event needs exactly one of person or family")
            if name == "add_family" and not (op.get("father") or op.get("mother")):
                raise ValueError("add_family needs a father or a mother")
            if name == "update_person":
                s = op.get("set")
                if not isinstance(s, dict) or not s or set(s) - PERSON_FIELDS:
                    raise ValueError(f"set takes only {sorted(PERSON_FIELDS)}")
                if "gender" in s and (not isinstance(s["gender"], str) or s["gender"] not in GENDER):
                    raise ValueError(f"gender is one of {sorted(GENDER)}")
            if name == "update_event":
                s = op.get("set")
                if not isinstance(s, dict) or not s or set(s) - EVENT_FIELDS:
                    raise ValueError(f"set takes only {sorted(EVENT_FIELDS)}")
                if "date" in s:
                    parse_date(s["date"])
                if s.get("place") and not (isinstance(s["place"], str) and (
                        s["place"].startswith("key:") or _GRAMPS_ID.match(s["place"]))):
                    raise ValueError(f"{s['place']!r} is neither key:<key> nor a Gramps ID (never a handle)")
            if name == "update_place":
                s = op.get("set")
                if not isinstance(s, dict) or not s or set(s) - PLACE_FIELDS:
                    raise ValueError(f"set takes only {sorted(PLACE_FIELDS)}")
                _check_coords(s)
                if "enclosed_by" in s and not (isinstance(s["enclosed_by"], str) and (
                        s["enclosed_by"].startswith("key:") or _GRAMPS_ID.match(s["enclosed_by"]))):
                    raise ValueError(f"{s['enclosed_by']!r} is neither key:<key> nor a Gramps ID (never a handle)")
                if s.get("enclosed_by") == op.get("place"):
                    raise ValueError("a place cannot enclose itself")
                if "name" in s and not (isinstance(s["name"], str) and s["name"].strip()):
                    raise ValueError("name is a non-empty string")
            if name == "update_note":
                f = op.get("set")
                if not isinstance(f, dict) or not f or set(f) - NOTE_FIELDS:
                    raise ValueError(f"set takes only {sorted(NOTE_FIELDS)}")
                for k in f:
                    if not isinstance(f[k], str) or not f[k].strip():
                        raise ValueError(f"{k} is a non-empty text")
                if "text" in f and not isinstance(op.get("expect"), str):
                    raise ValueError("changing the text needs expect: the text the note has now, so a note that "
                                     "changed since you read it is a conflict and not overwritten")
                if "text" not in f and "expect" in op:
                    raise ValueError("expect goes with set.text")
            if name == "update_citation":
                f = op.get("set")
                if not isinstance(f, dict) or not f or set(f) - CITATION_FIELDS:
                    raise ValueError(f"set takes only {sorted(CITATION_FIELDS)}")
                if "page" in f and not (isinstance(f["page"], str) and f["page"].strip()):
                    raise ValueError("page is a non-empty text")
                if "page" in f and not isinstance(op.get("expect"), str):
                    raise ValueError("changing the page needs expect: the page the citation has now")
                if "page" not in f and "expect" in op:
                    raise ValueError("expect goes with set.page")
                if "confidence" in f and f["confidence"] not in (0, 1, 2, 3, 4):
                    raise ValueError("confidence is 0-4")
                if "date" in f:
                    parse_date(f["date"])
            if name == "update_family":
                f = op.get("set")
                if not isinstance(f, dict) or not f or set(f) - FAMILY_FIELDS:
                    raise ValueError(f"set takes only {sorted(FAMILY_FIELDS)}")
                if not isinstance(f["type"], str) or not f["type"].strip():
                    raise ValueError("type is a non-empty text, e.g. Married")
            if name == "update_child_ref":
                f = op.get("set") or {}
                if not f and not op.get("confirm_probable"):
                    raise ValueError("give set (frel, mrel) or confirm_probable, or both")
                if not isinstance(f, dict) or set(f) - CHILD_REF_FIELDS:
                    raise ValueError(f"set takes only {sorted(CHILD_REF_FIELDS)}")
                for k in f:
                    if not isinstance(f[k], str) or not f[k].strip():
                        raise ValueError(f"{k} is a non-empty text, e.g. Birth")
                if "confirm_probable" in op and not (isinstance(op["confirm_probable"], str) and op["confirm_probable"].strip()):
                    raise ValueError("confirm_probable is the reason: the document that settles the link")
                if "expect" in op and not op.get("confirm_probable"):
                    raise ValueError("expect goes with confirm_probable")
            if name == "detach":
                cls, _ = _parse_target(op.get("target"))
                ocls, _, lst = _parse_object(op.get("object"))
                if cls not in CARRIES[lst]:
                    raise ValueError(f"a {cls} cannot carry a {lst[:-5]}")
            if name in ("add_tag", "remove_tag"):
                _tag_target(op.get("target"))
            if name == "move_event" and _event_owner(op.get("from")) == _event_owner(op.get("to")):
                raise ValueError("from and to are the same")
            if name == "set_attribute" and _parse_target(op.get("target"))[0] in ("ChildRef", "PersonRef"):
                raise ValueError("a child_ref or an association has no attributes")
            if name in ("add_repository", "update_repository"):
                f = op if name == "add_repository" else op.get("set")
                if name == "update_repository" and (not isinstance(f, dict) or not f or set(f) - REPOSITORY_FIELDS):
                    raise ValueError(f"set takes only {sorted(REPOSITORY_FIELDS)}")
                if "type" in f and not (isinstance(f["type"], str) and f["type"].strip()):
                    raise ValueError("type is a non-empty string, e.g. Archive")
                if "name" in f and not (isinstance(f["name"], str) and f["name"].strip()):
                    raise ValueError("name is a non-empty string")
                if "url" in f and not re.match(r"^https?://\S+$", str(f["url"])):
                    raise ValueError("url starts with http:// or https://")
                if "address" in f and (not isinstance(f["address"], dict) or not f["address"]
                                       or set(f["address"]) - ADDRESS_KEYS):
                    raise ValueError(f"address is an object with some of {sorted(ADDRESS_KEYS)}")
            if name == "link_repository" and "media_type" in op and not (
                    isinstance(op["media_type"], str) and op["media_type"].strip()):
                raise ValueError("media_type is a non-empty string, e.g. Book")
            if name == "add_place":
                _check_coords(op)
            if name == "attach_existing":
                lst = _parse_object(op.get("object"))[2]
            elif op.get("attach") and name in ("add_citation", "add_media", "add_note", "add_transcription"):
                lst = {"add_citation": "citation_list", "add_media": "media_list", "add_note": "note_list",
                       "add_transcription": "note_list"}[name]
            for spec in (op.get("attach") or []) if name in ("attach_existing", "add_citation", "add_media",
                                                             "add_note", "add_transcription") else []:
                cls, _ = _parse_target(spec)
                if cls not in CARRIES[lst]:
                    raise ValueError(f"a {cls} cannot carry a {lst[:-5]}")
            if name == "add_media" and not Path(op.get("file", "")).is_file():
                raise ValueError(f"file not found: {op.get('file')}")
            if name == "add_media" and Path(op.get("path", "")).is_absolute():
                raise ValueError("path is relative to the tree's media folder")
        except (ValueError, DateError, TypeError) as exc:  # TypeError: a value of the wrong kind, e.g. a list
            errors.append(f"{where}: {exc}")
    return errors


def _citation_differs(first: dict, op: dict) -> list[tuple[str, object, object]]:
    """(field, first's value, op's value) for each field `op` sets that differs from the citation `first` makes."""
    out = []
    if "confidence" in op and op["confidence"] != first.get("confidence", 2):
        out.append(("confidence", first.get("confidence", 2), op["confidence"]))
    if op.get("date"):
        try:
            if date_key(parse_date(op["date"])) != date_key(parse_date(first.get("date"))):
                out.append(("date", first.get("date") or "no date", op["date"]))
        except DateError:
            pass  # reported as an unreadable date
    return out


@dataclass
class Result:
    key: str
    op: str
    ruling: str
    status: str = "done"  # done | skip | conflict | error
    actions: list[str] = field(default_factory=list)
    answer_lost: bool = False  # done because an earlier try's write landed, though its answer never came
    reasons: list[str] = field(default_factory=list)  # why a `done` op is not in the tree yet: missing / differs




class Applier:
    def __init__(self, client: Client, *, retries: int = 5, retry_wait: float = 30.0, lock=None, tag: str | None = None,
                 verify: bool = False):
        self.c = client
        self.verify = verify  # a Gramps ID that was merged away resolves to its winner through the merge note
        self._merges: dict[str, list[tuple[str, str]]] | None = None  # class -> (merge note text, note handle)
        self.reasons: list[str] = []
        self.lock = lock  # a TreeLock to keep fresh before every try, during --apply
        self.run_tag = tag  # every object the run creates gets this tag, plus the op's own `tag`
        self._tags: dict[str, str] = {}  # tag name -> handle
        self._op_tags: list[str] = []
        self.retries = retries
        self.retry_wait = retry_wait
        self.keymap: dict[str, str] = {}  # key -> handle of the object it ended up as
        self.actions: list[str] = []
        self.wrote = False
        self._reread_found = False  # set by an op whose re-read found an earlier try's write in place
        self._names: dict[tuple[str, str], str] | None = None  # (first name, surname) -> Gramps ID
        # person handle -> the associations this run adds to that person and their ops, waiting for one PUT
        self._held: dict[str, dict] = {}
        self._holding: str | None = None  # set by an add_association whose write is held
        self._flushing = False
        self._unlink_notes: dict[str, list[str]] = {}  # op key -> link marks unlink_child still has to delete
        client.touch = self._touch

    # -- references -------------------------------------------------------

    def resolve(self, cls: str, ref: str) -> str:
        """A handle for "key:<key>" or a Gramps ID. Never accepts a bare handle."""
        if ref.startswith("key:"):
            key = ref[4:]
            handle = self.keymap.get(key) or stable_handle(key)
            if not self.c.exists(cls, handle):
                raise Conflict(f"{ref}: no {cls} made by that key")
            return handle
        if not _GRAMPS_ID.match(ref):
            raise Conflict(f"{ref!r} is neither key:<key> nor a Gramps ID")
        handle = self.c.handle_for_gramps_id(cls, ref)
        if handle is None and self.verify:
            handle = self._merged_into(cls, ref)
        if handle is None:
            raise Conflict(f"no {cls} with Gramps ID {ref}")
        return handle

    def _merged_into(self, cls: str, ref: str) -> str | None:
        """The handle of the object `ref` was merged into: the one whose note list holds the note our merge op
        wrote, `Merged <cls> <ref> ...`. Gramps moves that note on to the next winner when the winner is merged
        away too, so one lookup finds the object that holds it now. None when no object, or more than one, does."""
        if self._merges is None:
            self._merges = {}
            for n in self.c.list_all("Note", ["text"]):
                m = re.match(r"Merged (Person|Family|Event|Repository) (\S+) ", (n.get("text") or {}).get("string") or "")
                if m:
                    self._merges.setdefault(m[1], []).append((m[2], n["handle"]))
        notes = {h for loser, h in self._merges.get(cls, []) if loser == ref}
        if not notes:
            return None
        rows = [r for r in self.c.list_all(cls, ["note_list", "gramps_id"]) if notes & set(r.get("note_list") or [])]
        if len(rows) != 1:  # two holders: no way to tell which one is the winner, so the op stays a conflict
            return None
        winner = rows[0]
        self.actions.append(f"{cls} {ref} was merged into {self.label(winner)}: reading it there")
        return winner["handle"]

    def obj(self, cls: str, ref: str) -> dict:
        obj = self.c.get(cls, self.resolve(cls, ref))
        assert obj is not None
        return obj

    @staticmethod
    def label(obj: dict) -> str:
        return obj.get("gramps_id") or obj["handle"]

    # -- writes, each one recorded as an action ---------------------------

    def tag_handle(self, name: str) -> str:
        """The handle of the tag with this name, made if the tree has none."""
        if name not in self._tags:
            found = [t["handle"] for t in self.c.list_all("Tag", ["name"]) if t.get("name") == name]
            if found:
                self.c.get("Tag", found[0])  # read, so a snapshot apply checks it was not renamed since
                self._tags[name] = found[0]
            else:
                h = stable_handle("tag:" + name)
                if self.c.exists("Tag", h):  # made by a try whose answer was lost
                    self._tags[name] = h
                    return h
                self.c.create({"_class": "Tag", "handle": h, "name": name, "color": "#000000000000", "priority": 0})
                self.actions.append(f"create Tag {name!r}")
                if not self.c.dry_run and self.c.get_live("Tag", h) is None:
                    # a server that answered 201 and kept nothing: the block's objects would carry a tag that no
                    # list can filter by, and the block could no longer be found or rolled back by its tag
                    raise ApiError(f"Tag {name!r} was created but is not there when read back")
                self._tags[name] = h
        return self._tags[name]

    def create(self, obj: dict, extra: str = "") -> None:
        if self._op_tags:
            obj["tag_list"] = [self.tag_handle(t) for t in self._op_tags]
        self.actions.append(f"create {obj['_class']}{extra}: {json.dumps(obj, ensure_ascii=False)}")
        key = (self.c.context or {}).get("key") or ""
        key = next((k for k in (key, key + "#merge") if stable_handle(k) == obj["handle"]), None)
        self.reasons.append(f"missing: {obj['_class']} " + (f"key {key}" if key else f"handle {obj['handle']}"))
        self.c.create(obj)
        self.wrote = True

    def save(self, before: dict, after: dict) -> None:
        changes = []
        for k in sorted(set(before) | set(after)):
            if k.startswith("_") or before.get(k) == after.get(k):
                continue
            old, new = json.dumps(before.get(k), ensure_ascii=False), json.dumps(after.get(k), ensure_ascii=False)
            changes.append(f"{k}: {old} -> {new}")
            self.reasons.append(f"differs: {after['_class']} {self.label(after)} {k} {old} != {new}")
        self.actions.append(f"update {after['_class']} {self.label(after)}: " + "; ".join(changes))
        self.c.update(after)
        self.wrote = True

    def holder(self, cls: str, ref) -> tuple[dict, dict, str]:
        """(object to PUT, dict that carries the lists, label). A child_ref lives inside its family, an
        association (PersonRef) inside its person."""
        if cls == "PersonRef":
            person = self.obj("Person", ref[0])
            person.setdefault("_class", "Person")
            target = self.resolve("Person", ref[1])
            found = [r for r in person.get("person_ref_list") or []
                     if r.get("ref") == target and (ref[2] is None or r.get("rel") == ref[2])]
            what = f"association of Person {self.label(person)} with {ref[1]}" + (f" ({ref[2]})" if ref[2] else "")
            if not found:
                raise Conflict(f"no {what}")
            if len(found) > 1:
                raise Conflict(f"more than one {what}; say which with relation")
            return person, found[0], what
        if cls != "ChildRef":
            obj = self.obj(cls, ref)
            obj.setdefault("_class", cls)
            return obj, obj, f"{cls} {self.label(obj)}"
        family = self.obj("Family", ref[0])
        family.setdefault("_class", "Family")
        child = self.resolve("Person", ref[1])
        entry = next((c for c in family.get("child_ref_list") or [] if c["ref"] == child), None)
        if entry is None:
            raise Conflict(f"{ref[1]} is not a child of Family {self.label(family)}")
        return family, entry, f"child_ref of {ref[1]} in Family {self.label(family)}"

    def attach(self, specs: list, list_name: str, handle: str) -> None:
        for spec in specs:
            cls, ref = _parse_target(spec)
            target, carrier, label = self.holder(cls, ref)
            items = carrier.get(list_name) or []
            present = [i if isinstance(i, str) else i.get("ref") for i in items]
            if handle in present:
                self.actions.append(f"skip: {label} already has it in {list_name}")
                continue
            before = copy.deepcopy(target)
            new_item = handle if list_name != "media_list" else {"_class": "MediaRef", "ref": handle}
            carrier[list_name] = [*items, new_item]
            self.save(before, target)

    # -- the loop ---------------------------------------------------------

    def run(self, ops: list[dict]) -> list[Result]:
        results = []
        try:
            self._run(ops, results)
        finally:  # also on an exception no op caught: what earlier ops reported as held still gets written
            self.flush(list(self._held))
            self.c.context = {}
        return results

    def _run(self, ops: list[dict], results: list[Result]) -> None:
        for op in ops:
            res = Result(op["key"], op["op"], op["ruling"])
            self._op_tags = list(dict.fromkeys(t for t in [self.run_tag, *_tag_names(op.get("tag"))] if t))
            self.c.context = {"key": op["key"], "op": op["op"], "ruling": op["ruling"]}
            lost: list[str] = []  # "answer lost on try N: METHOD path", across every try of this op
            for attempt in range(1, self.retries + 1):
                if self.lock is not None:  # every try: a slow server can make one op outlast the lock
                    self.lock.refresh()
                self.actions, self.reasons, self.wrote, self._holding = [], [], False, None
                self._reread_found = False
                self.c.unknown_write = []
                # a dry run's plan of this try: dropped on a retry, or the next try reads it as already done
                planned = (dict(self.c.pending), set(self.c.deleted)) if self.c.dry_run else None
                try:
                    getattr(self, op["op"])(op)
                    res.status = "done" if self.wrote else "skip"
                    if lost and self.wrote and not self._reread_found:  # the re-read did not find the change, so this try wrote it
                        self.actions += [f"{line}; the re-read did not find it, so this try wrote it"
                                         for line in lost]
                    elif lost:
                        # an earlier try's write landed and its answer did not: this try read the change back
                        res.status, res.answer_lost = "done", True
                        self.actions += [f"{line}; the re-read shows the change in place" for line in lost]
                    break
                except Retryable as exc:
                    lost += [f"answer lost on try {attempt}: {w}" for w in self.c.unknown_write]
                    if planned is not None:
                        self.c.pending, self.c.deleted = planned
                    if attempt == self.retries:
                        res.status = "error"
                        self.actions += [f"{line}; the server may hold that write" for line in lost]
                        self.actions.append(f"error: {exc} (gave up after {attempt} tries)")
                        break
                    self.c.log(event="retry", attempt=attempt, error=str(exc))
                    time.sleep(self.retry_wait)
                except Conflict as exc:
                    res.status = "conflict"
                    self.actions += [f"{line}; the server may hold that write" for line in lost]
                    self.actions.append(f"conflict: {exc}")
                    break
                except ApiError as exc:
                    res.status = "error"
                    self.actions += [f"{line}; the server may hold that write" for line in lost]
                    self.actions.append(f"error: {exc}")
                    break
                except Exception as exc:  # a bug or an input validate() let through: stop here, never a traceback
                    res.status = "error"
                    self.actions += [f"{line}; the server may hold that write" for line in lost]
                    self.actions.append(f"error: {type(exc).__name__}: {exc}")
                    break
            res.actions, res.reasons = self.actions, self.reasons
            results.append(res)
            if self._holding:  # logged when the held write is made
                self._held[self._holding]["results"].append(res)
                continue
            self.c.log(event="op", status=res.status, actions=res.actions)
            if res.status in ("conflict", "error"):
                break

    # -- held association writes ------------------------------------------

    def _touch(self, cls: str | None, handle: str | None) -> None:
        """Before anything reads, writes or merges a person with held associations, write them."""
        if self._flushing or not self._held or cls not in (None, "Person"):
            return
        handles = list(self._held) if handle is None else [handle] if handle in self._held else []
        failed = self.flush(handles)
        if failed:
            raise ApiError(failed)

    def flush(self, handles: list[str]) -> str | None:
        """One PUT per person for the associations held for it. Every held op gets its final status here;
        returns what went wrong, or None. Each try starts from a fresh read and adds only the associations
        the person still lacks, so a try whose answer was lost is not written twice."""
        context, self._flushing, failed = self.c.context, True, None
        unknown, self.c.unknown_write = self.c.unknown_write, []  # a held write's lost answer is not the op's
        try:
            for handle in handles:
                held = self._held.pop(handle)
                results = held["results"]
                self.c.context = {"op": "add_association", "keys": [r.key for r in results]}
                error = None
                for attempt in range(1, self.retries + 1):
                    if self.lock is not None:
                        self.lock.refresh()
                    try:
                        person = self.c.get("Person", handle)
                        if person is None:
                            raise ApiError(f"Person {held['label']} is gone")
                        person.setdefault("_class", "Person")
                        refs = person.get("person_ref_list") or []
                        have = {(r.get("ref"), r.get("rel")) for r in refs}
                        missing = [n for n in held["new"] if (n["ref"], n["rel"]) not in have]
                        if missing:
                            person["person_ref_list"] = [*refs, *missing]
                            self.c.update(person)
                        self.c.log(event="held-write", person=held["label"], held=len(held["new"]), written=len(missing))
                        break
                    except Retryable as exc:
                        if attempt == self.retries:
                            error = f"{exc} (gave up after {attempt} tries)"
                            break
                        self.c.log(event="retry", attempt=attempt, error=str(exc))
                        time.sleep(self.retry_wait)
                    except ApiError as exc:
                        error = str(exc)
                        break
                for res in results:
                    if error is not None:
                        res.status = "error"
                        res.actions.append(f"error: the one write of the {len(results)} held associations of "
                                           f"Person {held['label']} failed: {error}")
                    self.c.context = {"key": res.key, "op": res.op, "ruling": res.ruling}
                    self.c.log(event="op", status=res.status, actions=res.actions)
                if error is not None and failed is None:
                    failed = f"the held associations of Person {held['label']} were not written: {error}"
        finally:
            self.c.context, self._flushing, self.c.unknown_write = context, False, unknown
        return failed

    def _existing(self, cls: str, op: dict) -> dict | None:
        """The object an earlier run made for this key, if any."""
        handle = stable_handle(op["key"])
        obj = self.c.get(cls, handle) if self.c.exists(cls, handle) else None
        if obj is not None:
            self.keymap[op["key"]] = handle
            self.actions.append(f"skip: {cls} {self.label(obj)} already made by key {op['key']!r}")
        return obj

    def _reuse(self, cls: str, candidates: list[dict], matched: str, checks, fix) -> dict | None:
        """Of the objects that match the op on `matched`, the one where every other field the op sets is the same.

        `checks(obj)` gives (field, same, theirs, ours) for each field the op sets; `fix(field)` says what to do.
        None when there is no candidate. When every candidate differs it is a Conflict: reusing one would put this
        op's fact on an object that says something else (a firm fact on a weak citation)."""
        differing = None
        for obj in candidates:
            rows = checks(obj)
            diff = next((r for r in rows if not r[1]), None)
            if diff is None:
                fields = ", ".join([matched, *(r[0] for r in rows)])
                self.actions.append(f"reused {self.label(obj)} (same {fields})")
                return obj
            differing = differing or (obj, diff)
        if differing is None:
            return None
        obj, (f, _, theirs, ours) = differing
        raise Conflict(f"{cls} {self.label(obj)} has the same {matched} with {f} {theirs}; this op says {ours}: {fix(f)}")

    def _check_gramps_id(self, cls: str, op: dict) -> None:
        if op.get("gramps_id") and self.c.handle_for_gramps_id(cls, op["gramps_id"]):
            raise Conflict(f"Gramps ID {op['gramps_id']} is taken by another {cls}")

    # -- ops --------------------------------------------------------------

    @staticmethod
    def _name_key(first: str, surname: str) -> tuple[str, str]:
        return (fold(first), fold(surname))

    def namesakes(self) -> dict[tuple[str, str], list[str]]:
        """(first name, surname) -> Gramps IDs (or key:<key> for people this run made)."""
        if self._names is None:
            self._names = {}
            for row in self.c.list_all("Person", ["primary_name", "gramps_id"]):
                n = row.get("primary_name") or {}
                surname = " ".join(s.get("surname", "") for s in n.get("surname_list") or [])
                name = self._name_key(n.get("first_name", ""), surname)
                self._names.setdefault(name, []).append(row.get("gramps_id") or row["handle"])
        return self._names

    def _compared(self, op) -> set[str]:
        """namesake_of as written, plus the Gramps ID of each key:<key> an earlier list already made."""
        refs = set(_ref_list(op.get("namesake_of") or []))
        for ref in [r for r in refs if r.startswith("key:")]:
            person = self.c.get("Person", self.keymap.get(ref[4:]) or stable_handle(ref[4:]))
            if person:
                refs.add(person.get("gramps_id") or person["handle"])
        return refs

    def add_person(self, op):
        if self._existing("Person", op):
            return
        self._check_gramps_id("Person", op)
        name = self._name_key(op["first_name"], op["surname"])
        same = self.namesakes().get(name, [])
        if same and not op.get("allow_namesake"):
            raise Conflict(
                f"{op['first_name']} {op['surname']} already exists as {', '.join(same)}; "
                "use that ID, or set allow_namesake with namesake_reason and namesake_of if this is another person"
            )
        unchecked = [x for x in same if x not in self._compared(op)]
        if unchecked:
            raise Conflict(
                f"{op['first_name']} {op['surname']} also exists as {', '.join(unchecked)}, "
                "which namesake_of does not name; compare with them too"
            )
        if same:
            self.actions.append(f"namesake of {', '.join(same)}, kept apart: {op['namesake_reason']}")
        h = stable_handle(op["key"])
        self.create(
            {
                "_class": "Person",
                "handle": h,
                "gramps_id": op.get("gramps_id", ""),
                "gender": GENDER[op.get("gender", "U")],
                "primary_name": {
                    "_class": "Name",
                    "type": "Birth Name",
                    "first_name": op["first_name"],
                    "surname_list": [
                        {"_class": "Surname", "surname": op["surname"], "primary": True}
                    ],
                },
            }
        )
        self.keymap[op["key"]] = h
        self._names.setdefault(name, []).append(op.get("gramps_id") or f"key:{op['key']}")

    def update_person(self, op):
        person = self.obj("Person", op["person"])
        person.setdefault("_class", "Person")
        before = copy.deepcopy(person)
        s = op["set"]
        if "gender" in s:
            person["gender"] = GENDER[s["gender"]]
        name = person["primary_name"]
        if "first_name" in s:
            name["first_name"] = s["first_name"]
        if "surname" in s:
            surnames = name.get("surname_list") or [{"_class": "Surname", "primary": True}]
            primary = next((x for x in surnames if x.get("primary")), surnames[0])
            primary["surname"] = s["surname"]
            name["surname_list"] = surnames
        if person == before:
            self.actions.append(f"skip: Person {self.label(person)} already has those values")
            return
        self.save(before, person)

    def update_event(self, op):
        event = self.obj("Event", op["event"])
        event.setdefault("_class", "Event")
        before = copy.deepcopy(event)
        s = op["set"]
        if "type" in s:
            event["type"] = s["type"]
        if "date" in s and date_key(event.get("date")) != date_key(parse_date(s["date"])):
            event["date"] = parse_date(s["date"])
        if "place" in s:
            event["place"] = self.resolve("Place", s["place"]) if s["place"] else ""
        if "description" in s:
            event["description"] = s["description"]
        if event == before:
            self.actions.append(f"skip: Event {self.label(event)} already has those values")
            return
        self.save(before, event)

    def update_place(self, op):
        place = self.obj("Place", op["place"])
        place.setdefault("_class", "Place")
        before = copy.deepcopy(place)
        s = op["set"]
        if "lat" in s:
            place["lat"], place["long"] = str(s["lat"]), str(s["long"])
        if "enclosed_by" in s:
            parent = self.resolve("Place", s["enclosed_by"])
            # compared with the parent the place has now, by handle: a place already under that one parent and no
            # other is left as it is, with whatever its PlaceRef carries, so a rerun is a skip
            if [r.get("ref") for r in place.get("placeref_list") or []] != [parent]:
                self._refuse_place_loop(place, parent)
                place["placeref_list"] = [{"_class": "PlaceRef", "ref": parent}]
        if "name" in s:
            name = s["name"].strip()
            current = place.get("name") or {}
            # compared with the name the place has now: the same text is a skip; only the value changes, so the
            # PlaceName keeps its date and language
            if current.get("value") != name:
                self._refuse_place_namesake(place, name)
                place["name"] = {**current, "_class": "PlaceName", "value": name}
        if place == before:
            self.actions.append(f"skip: Place {self.label(place)} already has those values")
            return
        self.save(before, place)

    def _refuse_place_namesake(self, place: dict, name: str) -> None:
        """A conflict when another place of the same type under the same parent already has that name, as add_place
        would match it: renaming would make a duplicate to merge, not a fix."""
        parent = next((r.get("ref") for r in place.get("placeref_list") or []), None)
        for row in self.c.list_all("Place", ["name", "place_type", "placeref_list", "gramps_id"]):
            if (row["handle"] != place["handle"]
                    and fold((row.get("name") or {}).get("value")) == fold(name)
                    and fold(str(row.get("place_type"))) == fold(str(place.get("place_type")))
                    and next((r.get("ref") for r in row.get("placeref_list") or []), None) == parent):
                raise Conflict(f"Place {row.get('gramps_id') or row['handle']} under the same parent is already "
                               f"called {(row.get('name') or {}).get('value')!r}; merge the two instead of renaming")

    def _refuse_place_loop(self, place: dict, parent: str) -> None:
        """A conflict when `parent` is the place itself or lies inside it, at any depth: the place would enclose
        itself. Walks every PlaceRef up from the new parent."""
        seen, todo = set(), [parent]
        while todo:
            h = todo.pop()
            if h == place["handle"]:
                p = self.c.get("Place", parent) or {}
                raise Conflict(f"Place {p.get('gramps_id') or parent} lies inside Place {self.label(place)}: "
                               "enclosing it there would make a loop")
            if h in seen:
                continue
            seen.add(h)
            todo += [r["ref"] for r in (self.c.get("Place", h) or {}).get("placeref_list") or [] if r.get("ref")]

    def add_event(self, op):
        target_cls = "Person" if op.get("person") else "Family"
        target = self.obj(target_cls, op.get("person") or op.get("family"))
        target.setdefault("_class", target_cls)
        role = op.get("role") or ("Primary" if target_cls == "Person" else "Family")
        date = parse_date(op.get("date"))
        etype = op["type"]
        event = self._existing("Event", op)
        if event is None:
            refs = target.get("event_ref_list") or []
            events = self.c.get_many("Event", [r["ref"] for r in refs]) if refs else []
            roles = {r["ref"]: r.get("role") for r in refs}
            same_date = []
            for ev in events:
                if str(ev.get("type")).lower() != etype.lower():
                    continue
                if date_key(ev.get("date")) == date_key(date):
                    same_date.append(ev)
                    continue
                if etype in SINGLE_VITAL and role == "Primary" and roles.get(ev["handle"]) == "Primary":
                    raise Conflict(
                        f"{target_cls} {self.label(target)} already has a {etype} with another date "
                        f"(Event {self.label(ev)}); fix that event instead of adding a second one"
                    )
            place = self.resolve("Place", op["place"]) if op.get("place") and same_date else None

            def checks(ev):
                rows = []
                if op.get("place"):
                    p = self.c.get("Place", ev["place"]) if ev.get("place") and ev["place"] != place else None
                    shown = (p or {}).get("gramps_id") or ("none" if not ev.get("place") else op["place"])
                    rows.append(("place", ev.get("place") == place, shown, op["place"]))
                if op.get("description"):
                    theirs = (ev.get("description") or "").strip()
                    rows.append(("description", theirs == op["description"].strip(), repr(theirs), repr(op["description"])))
                return rows

            ev = self._reuse(
                "Event", same_date, f"type and date on {target_cls} {self.label(target)}", checks,
                lambda f: f"if it is that event, correct it with update_event; if it is another {etype}, give this op "
                          "its date so the two can be told apart")
            if ev is not None:
                self.keymap[op["key"]] = ev["handle"]
                return
            self._check_gramps_id("Event", op)
            h = stable_handle(op["key"])
            place = self.resolve("Place", op["place"]) if op.get("place") else ""
            self.create(
                {
                    "_class": "Event",
                    "handle": h,
                    "gramps_id": op.get("gramps_id", ""),
                    "type": etype,
                    "date": date,
                    "place": place,
                    "description": op.get("description", ""),
                }
            )
            self.keymap[op["key"]] = h
            event = {"handle": h}
        # link (also finishes a run that crashed between create and link)
        refs = target.get("event_ref_list") or []
        if any(r["ref"] == event["handle"] for r in refs):
            self.actions.append(f"skip: {target_cls} {self.label(target)} already links it")
            return
        before = copy.deepcopy(target)
        target["event_ref_list"] = [*refs, {"_class": "EventRef", "ref": event["handle"], "role": role}]
        index_key = SINGLE_VITAL.get(etype)
        if target_cls == "Person" and index_key and role == "Primary" and target.get(index_key, -1) == -1:
            target[index_key] = len(refs)
        self.save(before, target)

    def add_family(self, op):
        father = self.resolve("Person", op["father"]) if op.get("father") else None
        mother = self.resolve("Person", op["mother"]) if op.get("mother") else None
        children = [self.resolve("Person", c) for c in op.get("children") or []]
        family = self._existing("Family", op)
        if family is None:
            parent = self.c.get("Person", father or mother)
            fams = self.c.get_many("Family", parent.get("family_list") or [])
            for fam in fams:
                if (fam.get("father_handle") or None) == father and (fam.get("mother_handle") or None) == mother:
                    family = fam
                    self.keymap[op["key"]] = fam["handle"]
                    self.actions.append(f"skip: these parents already have Family {self.label(fam)}")
                    break
        if family is None:
            self._check_gramps_id("Family", op)
            h = stable_handle(op["key"])
            self.create(
                {
                    "_class": "Family",
                    "handle": h,
                    "gramps_id": op.get("gramps_id", ""),
                    "father_handle": father,
                    "mother_handle": mother,
                    "type": op.get("type", "Unknown"),
                    "child_ref_list": [
                        {"_class": "ChildRef", "ref": c, "frel": "Birth", "mrel": "Birth"} for c in children
                    ],
                }
            )
            self.keymap[op["key"]] = h
            return
        for child in children:
            self._link_child(family["handle"], child, "Birth", "Birth")

    def _link_child(self, family_handle: str, child: str, frel: str, mrel: str):
        family = self.c.get("Family", family_handle)
        family.setdefault("_class", "Family")
        refs = family.get("child_ref_list") or []
        if any(r["ref"] == child for r in refs):
            self.actions.append(f"skip: Family {self.label(family)} already has that child")
            return
        before = copy.deepcopy(family)
        family["child_ref_list"] = [*refs, {"_class": "ChildRef", "ref": child, "frel": frel, "mrel": mrel}]
        self.save(before, family)

    def add_association(self, op):
        """A PersonRef from person to target with this relation; Gramps Web's DNA view reads relation "DNA".

        The write is held: every association the run adds to one person goes in a single PUT, made at the end
        of the run or as soon as anything else reads, writes or merges that person. A PUT rewrites the whole
        person, so one per association made a list of n associations cost n rewrites of a growing person.
        """
        target = self.resolve("Person", op["target"])
        handle = self.resolve("Person", op["person"])
        held = self._held.get(handle)
        person = held["person"] if held else self.c.get("Person", handle)
        refs = person.get("person_ref_list") or []
        if any(r.get("ref") == target and r.get("rel") == op["relation"] for r in refs):
            self.actions.append(f"skip: Person {self.label(person)} already has a {op['relation']} association with {op['target']}")
            return
        new = {"_class": "PersonRef", "ref": target, "rel": op["relation"], "citation_list": [], "note_list": [], "private": False}
        if held is None:
            held = self._held[handle] = {"person": person, "label": self.label(person), "new": [], "results": []}
        person["person_ref_list"] = [*refs, new]
        held["new"].append(new)
        self.actions.append(f"update Person {held['label']}: person_ref_list +{json.dumps([new], ensure_ascii=False)} "
                            "(one write with the other associations of this person)")
        self.reasons.append(f"missing: Person {held['label']} {op['relation']} association with {op['target']}")
        self.wrote = True
        self._holding = handle

    def link_child(self, op):
        family = self.resolve("Family", op["family"])
        child = self.resolve("Person", op["child"])
        self._link_child(family, child, op.get("frel", "Birth"), op.get("mrel", "Birth"))
        if op.get("probable"):
            # the mark lives on the link itself: a Research note "[probable] <reason>" on the child_ref
            self.add_note(
                {
                    "key": op["key"] + "#probable",
                    "type": "Research",
                    "text": f"{PROBABLE} {op['probable_reason'].strip()}",
                    "attach": [{"child_ref": {"family": op["family"], "child": op["child"]}}],
                },
                marker=PROBABLE,
            )

    def unlink_child(self, op):
        """Take a child out of a family: the ChildRef off the family, then the family off the child's parent
        families, then the reason on the child as a Research note "[unlinked from Family F] <reason>". Nobody is
        deleted, the family neither. A mark on the link ([probable], [confirmed], [wrong link]) that nothing else
        holds goes with it; any other note or citation on the link is a conflict, or with drop_link_sources an
        orphan. The family goes first, so --revert, newest write first, restores the child after its family."""
        family = self.obj("Family", op["family"])
        family.setdefault("_class", "Family")
        child = self.obj("Person", op["child"])
        fam, kid = family["handle"], child["handle"]
        flabel, clabel = self.label(family), self.label(child)
        entry = next((r for r in family.get("child_ref_list") or [] if r.get("ref") == kid), None)
        listed = fam in (child.get("parent_family_list") or [])
        retry = op["key"] in self._unlink_notes  # an earlier try of this op wrote, and its answer was lost
        if entry is None and not listed and not retry:
            self.actions.append(f"skip: Person {clabel} is not a child of Family {flabel}")
            return
        drop, other = [], []  # every check before the first write: a conflict leaves the server as it was
        if entry is not None:
            rest = json.dumps({**{k: v for k, v in family.items() if not k.startswith("_")},  # not _read: a copy
                               "child_ref_list": [r for r in family["child_ref_list"] if r is not entry]})
            notes = {n["handle"]: n for n in self.c.get_many("Note", entry.get("note_list") or [])}
            for h in entry.get("note_list") or []:
                n = notes.get(h)
                text = ((n or {}).get("text") or {}).get("string") or ""
                if n and text.startswith(LINK_MARKS) and h not in rest and self._referrers("Note", h) == {("family", fam)}:  # empty: the server named no holder
                    drop.append(n)
                else:
                    other.append(f"Note {self.label(n) if n else h}")
            cits = {c["handle"]: c for c in self.c.get_many("Citation", entry.get("citation_list") or [])}
            other += [f"Citation {self.label(cits[h]) if h in cits else h}" for h in entry.get("citation_list") or []]
        if other and not op.get("drop_link_sources"):
            raise Conflict(f"the link of Person {clabel} to Family {flabel} carries {', '.join(other)}, which would be "
                           "lost with it; move them first, or set drop_link_sources: true to detach them (they stay in "
                           "the tree as orphans gwt-check lists)")
        marks = self._unlink_notes.setdefault(op["key"], [])
        marks += [n["handle"] for n in drop if n["handle"] not in marks]
        if entry is not None:
            before = copy.deepcopy(family)
            family["child_ref_list"] = [r for r in family["child_ref_list"] if r is not entry]
            self.save(before, family)
            if other:
                self.actions.append(f"detached from the link, left in the tree: {', '.join(other)}")
            if not listed and not retry:
                self.actions.append(f"the tree was inconsistent: Person {clabel} did not list Family {flabel} as a "
                                    "parent family; fixed the family's side only")
        self.add_note({"key": op["key"] + "#unlinked", "type": "Research",
                       "text": f"[unlinked from Family {flabel}] {op['reason'].strip()}",
                       "attach": [{"person": op["child"]}]})
        child = self.c.get("Person", kid)  # again: the note went on it, and the family write may have changed it
        child.setdefault("_class", "Person")
        if fam in (child.get("parent_family_list") or []):
            before = copy.deepcopy(child)
            child["parent_family_list"] = [h for h in child["parent_family_list"] if h != fam]
            self.save(before, child)
            if entry is None and not retry:
                self.actions.append(f"the tree was inconsistent: Family {flabel} did not list Person {clabel} as a "
                                    "child; fixed the person's side only")
        elif listed:
            self.actions.append(f"Person {clabel} no longer lists Family {flabel}: the server took it off with the family")
        for h in marks:
            self._drop_mark(h)
        del self._unlink_notes[op["key"]]
        if entry is not None and not (family.get("father_handle") or family.get("mother_handle")
                                      or family.get("child_ref_list")):
            self.actions.append(f"warning: Family {flabel} now has no father, no mother and no children; it is not "
                                "deleted")

    def _referrers(self, cls: str, handle: str) -> set[tuple[str, str]]:
        """(lower-case class, handle) of every object that holds this one: the server's backlinks, or on an
        offline snapshot a search of the snapshot and this run's planned writes."""
        if not self.c.offline:
            links = (self.c.get_live(cls, handle, backlinks=True) or {}).get("backlinks") or {}
            return {(k, h) for k, hs in links.items() for h in hs or []}
        objs = {(c, h): o for c, rows in (self.c.snapshot or {}).items() for h, o in rows.items()}
        objs.update(self.c.pending)
        return {(c.lower(), h) for (c, h), o in objs.items()
                if h != handle and (c, h) not in self.c.deleted and handle in json.dumps(o)}

    def _drop_mark(self, handle: str) -> None:
        """Delete a link mark unlink_child took off its link, unless something has taken it up since."""
        if not self.c.exists("Note", handle):
            return
        note = self.c.get("Note", handle)
        if not self.c.dry_run:  # a dry run's family write is only planned, so the server still names the family
            held = self._referrers("Note", handle)
            if held:
                self.actions.append(f"kept Note {self.label(note)}: still held by "
                                    + ", ".join(f"{c} {h}" for c, h in sorted(held)))
                return
        self.actions.append(f"delete Note {self.label(note)}: {((note.get('text') or {}).get('string') or '')!r}")
        self.reasons.append(f"missing: delete Note {self.label(note)}")
        self.c.remove("Note", handle)
        self.wrote = True

    def update_note(self, op):
        """Correct a note the tree has. A new text needs `expect`, the text it has now: a note someone changed
        since you read it is a Conflict, never overwritten. The old text stays in the log line."""
        self._update_note(self.obj("Note", op["note"]), op["set"], op.get("expect"))

    def _update_note(self, note: dict, s: dict, expect: str | None) -> None:
        note.setdefault("_class", "Note")
        before = copy.deepcopy(note)
        text = (note.get("text") or {}).get("string") or ""
        if "text" in s and text != s["text"]:
            if text != expect:
                raise Conflict(f"Note {self.label(note)} no longer reads as expected (it reads {text[:80]!r}...); read it again")
            note["text"] = {**(note.get("text") or {"_class": "StyledText"}), "string": s["text"], "tags": []}
        if "type" in s:
            note["type"] = s["type"]
        if note == before:
            self.actions.append(f"skip: Note {self.label(note)} already has those values")
            return
        self.save(before, note)

    def update_citation(self, op):
        cit = self.obj("Citation", op["citation"])
        cit.setdefault("_class", "Citation")
        before = copy.deepcopy(cit)
        s = op["set"]
        if "page" in s and (cit.get("page") or "").strip() != s["page"].strip():
            if (cit.get("page") or "").strip() != op["expect"].strip():
                raise Conflict(f"Citation {self.label(cit)} no longer has the page {op['expect']!r} (it has {cit.get('page')!r})")
            cit["page"] = s["page"].strip()
        if "confidence" in s:
            cit["confidence"] = int(s["confidence"])
        if "date" in s and date_key(cit.get("date")) != date_key(parse_date(s["date"])):
            cit["date"] = parse_date(s["date"])
        if cit == before:
            self.actions.append(f"skip: Citation {self.label(cit)} already has those values")
            return
        self.save(before, cit)

    def update_family(self, op):
        family = self.obj("Family", op["family"])
        family.setdefault("_class", "Family")
        before = copy.deepcopy(family)
        family["type"] = op["set"]["type"]
        if family == before:
            self.actions.append(f"skip: Family {self.label(family)} already has that type")
            return
        self.save(before, family)

    def update_child_ref(self, op):
        """Change a parent link's relation (frel, mrel), and/or settle a [probable] link: its mark becomes
        "[confirmed] <reason> (was: <the old reason>)", so the history of the doubt stays on the link."""
        family, entry, label = self.holder("ChildRef", (op["family"], op["child"]))
        before = copy.deepcopy(family)
        for k, v in (op.get("set") or {}).items():
            entry[k] = v
        reason = (op.get("confirm_probable") or "").strip()
        marked = []
        if reason:  # every check before the first write: a conflict leaves the server as it was
            notes = self.c.get_many("Note", entry.get("note_list") or [])
            marked = [n for n in notes if ((n.get("text") or {}).get("string") or "").startswith(PROBABLE)]
            done = [n for n in notes if ((n.get("text") or {}).get("string") or "").startswith(CONFIRMED)]
            if not marked and not done:
                raise Conflict(f"{label} has no [probable] note to confirm")
            if len(marked) > 1:
                raise Conflict(f"{label} has more than one [probable] note")
            if marked and "expect" in op and marked[0]["text"]["string"] != op["expect"]:
                raise Conflict(f"the [probable] note of {label} no longer reads as expected "
                               f"(it reads {marked[0]['text']['string'][:80]!r}...)")
            if not marked:
                self.actions.append(f"skip: {label} is already confirmed (Note {self.label(done[0])})")
        if family != before:
            self.save(before, family)
        elif not marked:
            if not reason:
                self.actions.append(f"skip: {label} already has those relations")
            return
        if marked:
            cur = marked[0]["text"]["string"]
            old = cur[len(PROBABLE):].strip()
            # by handle: a note made earlier in this list has no Gramps ID this run knows
            note = self.c.get("Note", marked[0]["handle"])
            self._update_note(note, {"text": f"{CONFIRMED} {reason} (was: {PROBABLE} {old})"}, cur)

    def detach(self, op):
        """Take a note, citation or media reference off a target. The object stays in the tree (an orphan
        that gwt-check lists), so nothing is lost and attach_existing can put it back."""
        ocls, oref, lst = _parse_object(op["object"])
        handle = self.resolve(ocls, oref)
        cls, ref = _parse_target(op["target"])
        target, carrier, label = self.holder(cls, ref)
        items = carrier.get(lst) or []
        keep = [i for i in items if (i if isinstance(i, str) else i.get("ref")) != handle]
        if len(keep) == len(items):
            self.actions.append(f"skip: {label} does not have it in {lst}")
            return
        for i in items:
            if isinstance(i, dict) and i.get("ref") == handle and (i.get("rect") or any(i.get(o) for o in REF_OWN_LISTS)):
                raise Conflict(f"{label}: its reference to {oref} carries a region, notes, citations or attributes "
                               "that attach_existing cannot put back; not detached")
        before = copy.deepcopy(target)
        carrier[lst] = keep
        self.save(before, target)

    def add_place(self, op):
        if self._existing("Place", op):
            return
        parent = self.resolve("Place", op["enclosed_by"]) if op.get("enclosed_by") else None
        name = op["name"].strip()
        for row in self.c.list_all("Place", ["name", "place_type", "placeref_list", "gramps_id"]):
            row_parent = next((r.get("ref") for r in row.get("placeref_list") or []), None)
            if (
                fold((row.get("name") or {}).get("value")) == fold(name)
                and fold(str(row.get("place_type"))) == fold(op["type"])
                and row_parent == parent
            ):
                self.keymap[op["key"]] = row["handle"]
                self.actions.append(
                    f"skip: Place {row.get('gramps_id') or row['handle']} "
                    f"{(row.get('name') or {}).get('value')!r} ({row.get('place_type')}) matches {name!r}"
                )
                return
        self._check_gramps_id("Place", op)
        h = stable_handle(op["key"])
        obj = {
            "_class": "Place",
            "handle": h,
            "gramps_id": op.get("gramps_id", ""),
            "name": {"_class": "PlaceName", "value": name},
            "place_type": op["type"],
            "placeref_list": [{"_class": "PlaceRef", "ref": parent}] if parent else [],
        }
        if "lat" in op:
            obj["lat"], obj["long"] = str(op["lat"]), str(op["long"])
        self.create(obj)
        self.keymap[op["key"]] = h

    def add_source(self, op):
        if self._existing("Source", op):
            return
        title = op["title"].strip()
        rows = [r for r in self.c.list_all("Source", ["title", "author", "pubinfo", "gramps_id"])
                if (r.get("title") or "").strip() == title]

        def checks(row):
            return [(f, (row.get(f) or "").strip() == op[f].strip(), repr(row.get(f) or ""), repr(op[f]))
                    for f in ("author", "pubinfo") if op.get(f)]

        found = self._reuse("Source", rows, "title", checks,
                            lambda f: f"if it is that source, set its {f} in Gramps and rerun; if it is another "
                                      "source, give it a title that tells them apart")
        if found is not None:
            self.keymap[op["key"]] = found["handle"]
            return
        self._check_gramps_id("Source", op)
        h = stable_handle(op["key"])
        obj = {"_class": "Source", "handle": h, "gramps_id": op.get("gramps_id", ""), "title": title}
        for f in ("author", "pubinfo", "abbrev"):
            if op.get(f):
                obj[f] = op[f]
        self.create(obj)
        self.keymap[op["key"]] = h

    @staticmethod
    def _repo_fields(obj: dict, f: dict) -> None:
        """Put url and address on a repository: a url already there is kept, the first address is completed."""
        if "url" in f and not any(u.get("path") == f["url"] for u in obj.get("urls") or []):
            obj["urls"] = [*(obj.get("urls") or []), {"_class": "Url", "path": f["url"], "type": "Web Link", "desc": ""}]
        if "address" in f:
            addrs = obj.get("address_list") or []
            if not addrs:
                addrs = [{"_class": "Address"}]
            addrs[0] = {**addrs[0], **f["address"]}
            obj["address_list"] = addrs

    def add_repository(self, op):
        if self._existing("Repository", op):
            return
        name = op["name"].strip()
        for row in self.c.list_all("Repository", ["name", "gramps_id"]):
            if fold(row.get("name") or "") == fold(name):
                self.keymap[op["key"]] = row["handle"]
                self.actions.append(f"skip: Repository {row.get('gramps_id') or row['handle']} has that name")
                return
        self._check_gramps_id("Repository", op)
        h = stable_handle(op["key"])
        obj = {"_class": "Repository", "handle": h, "gramps_id": op.get("gramps_id", ""), "name": name, "type": op["type"]}
        self._repo_fields(obj, op)
        self.create(obj)
        self.keymap[op["key"]] = h

    def update_repository(self, op):
        repo = self.obj("Repository", op["repository"])
        repo.setdefault("_class", "Repository")
        before = copy.deepcopy(repo)
        s = op["set"]
        if "name" in s:
            new = s["name"].strip()
            for row in self.c.list_all("Repository", ["name", "gramps_id"]):
                if row["handle"] != repo["handle"] and fold(row.get("name") or "") == fold(new):
                    raise Conflict(f"Repository {row.get('gramps_id') or row['handle']} already has the name {new!r}; "
                                   "merge them with merge_repository")
            repo["name"] = new
        if "type" in s:
            repo["type"] = s["type"]
        self._repo_fields(repo, s)
        if repo == before:
            self.actions.append(f"skip: Repository {self.label(repo)} already has those values")
            return
        self.save(before, repo)

    def link_repository(self, op):
        source = self.obj("Source", op["source"])
        repo = self.resolve("Repository", op["repository"])
        source.setdefault("_class", "Source")
        before = copy.deepcopy(source)
        refs = source.setdefault("reporef_list", [])
        wanted = {k: op[k] for k in ("call_number", "media_type") if k in op}
        for r in refs:
            if r.get("ref") == repo:
                r.update({k: v for k, v in wanted.items() if r.get(k) != v})
                if source == before:
                    self.actions.append(f"skip: Source {self.label(source)} already lists that repository")
                    return
                self.save(before, source)
                return
        refs.append({"_class": "RepoRef", "ref": repo, "call_number": "", **wanted})
        self.save(before, source)

    def unlink_repository(self, op):
        source = self.obj("Source", op["source"])
        repo = self.resolve("Repository", op["repository"])
        source.setdefault("_class", "Source")
        before = copy.deepcopy(source)
        source["reporef_list"] = [r for r in source.get("reporef_list") or [] if r.get("ref") != repo]
        if source == before:
            self.actions.append(f"skip: Source {self.label(source)} does not list that repository")
            return
        self.save(before, source)

    def merge_repository(self, op):
        """Sources move from the loser to the winner, then the loser goes. Only sources point at a repository, so
        there is no merge call to lean on: the references are moved here, one source at a time."""
        if self._merge_done("Repository", op):
            return
        winner, loser = self.obj("Repository", op["winner"]), self.obj("Repository", op["loser"])
        for o in (winner, loser):
            o.setdefault("_class", "Repository")
        if winner["handle"] == loser["handle"]:
            raise Conflict("winner and loser are the same repository")
        self._merge_note(op, "Repository", f"Repository {self.label(loser)} ({loser.get('name', '')})")
        winner = self.obj("Repository", op["winner"])
        winner.setdefault("_class", "Repository")
        before = copy.deepcopy(winner)
        for u in loser.get("urls") or []:
            if not any(x.get("path") == u.get("path") for x in winner.get("urls") or []):
                winner["urls"] = [*(winner.get("urls") or []), u]
        for a in loser.get("address_list") or []:
            if a not in (winner.get("address_list") or []):
                winner["address_list"] = [*(winner.get("address_list") or []), a]
        for n in loser.get("note_list") or []:
            if n not in (winner.get("note_list") or []):
                winner["note_list"] = [*(winner.get("note_list") or []), n]
        if winner != before:
            self.save(before, winner)
        for row in self.c.list_all("Source", ["reporef_list", "gramps_id"]):
            if not any(r.get("ref") == loser["handle"] for r in row.get("reporef_list") or []):
                continue
            src = self.c.get("Source", row["handle"])
            src.setdefault("_class", "Source")
            b = copy.deepcopy(src)
            out = []
            for r in src.get("reporef_list") or []:
                if r.get("ref") != loser["handle"]:
                    out.append(r)
                    continue
                twin = next((x for x in src["reporef_list"] if x.get("ref") == winner["handle"]), None)
                if twin is None:
                    out.append({**r, "ref": winner["handle"]})
                else:  # the source already lists the winner: keep what the loser's entry knew that the winner's lacks
                    for k in ("call_number", "media_type"):
                        if r.get(k) and not twin.get(k):
                            twin[k] = r[k]
                    notes = twin.get("note_list") or []
                    twin["note_list"] = notes + [n for n in r.get("note_list") or [] if n not in notes]
            src["reporef_list"] = out
            self.save(b, src)
        self.actions.append(f"delete Repository {self.label(loser)}")
        self.reasons.append(f"missing: merge Repository {self.label(loser)} into {self.label(winner)}")
        self.c.remove("Repository", loser["handle"])
        self.wrote = True

    def add_citation(self, op):
        source = self.resolve("Source", op["source"])
        page = op["page"].strip()
        cit = self._existing("Citation", op)
        handle = cit["handle"] if cit else None
        if handle is None:
            rows = [r for r in self.c.list_all("Citation", ["source_handle", "page", "confidence", "date", "gramps_id"])
                    if r.get("source_handle") == source and (r.get("page") or "").strip() == page]

            def checks(row):
                out = []
                if "confidence" in op:
                    theirs = row.get("confidence")
                    theirs = 2 if theirs is None else theirs
                    out.append(("confidence", theirs == op["confidence"], theirs, op["confidence"]))
                if op.get("date"):
                    out.append(("date", date_key(row.get("date")) == date_key(parse_date(op["date"])),
                                _date_text(row.get("date")), op["date"]))
                return out

            def fix(f):
                return (f"{'raise it' if f == 'confidence' else 'correct it'} with update_citation, or give this "
                        "role its own page")

            found = self._reuse("Citation", rows, "source and page", checks, fix)
            if found is not None:
                handle = found["handle"]
                self.keymap[op["key"]] = handle
        if handle is None:
            self._check_gramps_id("Citation", op)
            handle = stable_handle(op["key"])
            self.create(
                {
                    "_class": "Citation",
                    "handle": handle,
                    "gramps_id": op.get("gramps_id", ""),
                    "source_handle": source,
                    "page": page,
                    "confidence": int(op.get("confidence", 2)),
                    "date": parse_date(op.get("date")),
                }
            )
            self.keymap[op["key"]] = handle
        self.attach(op["attach"], "citation_list", handle)

    def add_media(self, op):
        data = Path(op["file"]).read_bytes()
        checksum = hashlib.md5(data).hexdigest()
        mime = op.get("mime") or mimetypes.guess_type(op["file"])[0]
        if not mime:
            raise Conflict(f"cannot tell the MIME type of {op['file']}; set mime")
        media = self._existing("Media", op)
        handle = media["handle"] if media else None
        if media is not None and media.get("checksum") != checksum:
            raise Conflict(f"Media {self.label(media)} made by this key holds another file")
        if handle is None:
            for row in self.c.list_all("Media", ["checksum", "gramps_id", "path"]):
                if row.get("checksum") == checksum:
                    handle = row["handle"]
                    self.keymap[op["key"]] = handle
                    self.actions.append(
                        f"skip: Media {row.get('gramps_id') or handle} has the same checksum ({row.get('path')})"
                    )
                    break
            else:
                self._check_gramps_id("Media", op)
                handle = stable_handle(op["key"])
                self.create(
                    {
                        "_class": "Media",
                        "handle": handle,
                        "gramps_id": op.get("gramps_id", ""),
                        "path": op["path"],
                        "checksum": checksum,
                        "mime": mime,
                        "desc": op.get("desc", ""),
                    }
                )
                self.keymap[op["key"]] = handle
                media = {"handle": handle}
        if media is not None:
            # ours: make sure the file is stored, also after a crash right after create
            outcome = self.c.upload_missing_file(handle, data, mime)
            self.actions.append(f"file: {outcome}")
            if outcome == "uploaded":
                self.wrote = True
        self.attach(op["attach"], "media_list", handle)

    def add_note(self, op, marker: str | None = None):
        """A note on each target; with `marker`, any note on a target that starts with it counts as this one."""
        text = op["text"]
        note = self._existing("Note", op)
        handle = note["handle"] if note else None
        if handle is None:
            for spec in op["attach"]:
                cls, ref = _parse_target(spec)
                _, carrier, label = self.holder(cls, ref)
                notes = self.c.get_many("Note", carrier.get("note_list") or [])
                for n in notes:
                    found = (n.get("text") or {}).get("string") or ""
                    if marker and found.startswith(marker):  # the mark itself, whatever its reason says
                        handle = n["handle"]
                        self.keymap[op["key"]] = handle
                        self.actions.append(f"skip: {label} already has that mark as Note {self.label(n)}")
                        break
                if handle:
                    break
                same = [n for n in notes if ((n.get("text") or {}).get("string") or "") == text]
                found = self._reuse(
                    "Note", same, f"text on {label}",
                    lambda n: [("type", str(n.get("type")).lower() == op["type"].lower(), n.get("type"), op["type"])]
                    if op.get("type") else [],
                    lambda f: "if it is that note, correct it with update_note; if not, give this one its own text")
                if found is not None:
                    handle = found["handle"]
                    self.keymap[op["key"]] = handle
                    break
        if handle is None:
            self._check_gramps_id("Note", op)
            handle = stable_handle(op["key"])
            self.create(
                {
                    "_class": "Note",
                    "handle": handle,
                    "gramps_id": op.get("gramps_id", ""),
                    "type": op.get("type", "General"),
                    "format": 0,
                    "text": {"_class": "StyledText", "string": text, "tags": []},
                }
            )
            self.keymap[op["key"]] = handle
        self.attach(op["attach"], "note_list", handle)

    def add_transcription(self, op):
        """A transcript as a Transcript note, never as media; the margin reading is part of the text.

        `margin_reading` is required: what the margins of the page say (annotations, later notes,
        corrections), or the word "none" once you have looked. A transcript made without looking at the
        margin is how a legitimate-looking entry ends up with the wrong person.
        """
        reading = str(op["margin_reading"]).strip()
        text = f"{op['text'].rstrip()}\n\nMargin reading: {reading}"
        self.add_note(
            {"op": "add_note", "key": op["key"], "ruling": op["ruling"], "type": "Transcript",
             "text": text, "attach": op["attach"], "gramps_id": op.get("gramps_id", "")}
        )

    def attach_existing(self, op):
        """Link a citation, media object or note the tree already has to more targets.

        For what an import left with no reference at all (orphans) and for a document that belongs
        to a second person: add_citation, add_media and add_note only make new objects.
        """
        cls, ref, lst = _parse_object(op["object"])
        self.attach(op["attach"], lst, self.resolve(cls, ref))

    def add_tag(self, op):
        """Put a tag (made if the tree has none) on an object the tree already has: a research mark such as
        `Hypothesis`. Nothing else on the object changes."""
        cls, ref = _tag_target(op["target"])
        target = self.obj(cls, ref)
        target.setdefault("_class", cls)
        handle = self.tag_handle(op["name"].strip())
        if handle in (target.get("tag_list") or []):
            self.actions.append(f"skip: {cls} {self.label(target)} already has the tag {op['name'].strip()!r}")
            return
        before = copy.deepcopy(target)
        target["tag_list"] = [*(target.get("tag_list") or []), handle]
        self.save(before, target)

    def remove_tag(self, op):
        """Take one tag off an object the tree already has: a weak mark once the fact is proven. The tag itself
        stays in the tree, and so does everything else on the object. A name the tree has no tag for is a conflict
        (a typo would otherwise pass as a skip); an object without the tag is a skip."""
        cls, ref = _tag_target(op["target"])
        target = self.obj(cls, ref)
        target.setdefault("_class", cls)
        name = op["name"].strip()
        handles = {t["handle"] for t in self.c.list_all("Tag", ["name"]) if t.get("name") == name}
        if not handles:
            raise Conflict(f"the tree has no tag {name!r}; check the name")
        for h in handles:
            self.c.get("Tag", h)  # read, so a snapshot apply checks it was not renamed since
        tags = target.get("tag_list") or []
        keep = [h for h in tags if h not in handles]
        if len(keep) == len(tags):
            self.actions.append(f"skip: {cls} {self.label(target)} does not have the tag {name!r}")
            return
        before = copy.deepcopy(target)
        target["tag_list"] = keep
        self.save(before, target)

    def _reindex(self, person: dict, old_refs: list) -> None:
        """birth_ref_index and death_ref_index point at the same event after event_ref_list changed. When that
        event left the list, they point at the first other Primary event of that type the person still lists, and
        become -1 only when there is none."""
        refs = person.get("event_ref_list") or []
        for etype, key in SINGLE_VITAL.items():
            i = person.get(key, -1)
            h = old_refs[i].get("ref") if isinstance(i, int) and 0 <= i < len(old_refs) else None
            new = next((n for n, r in enumerate(refs) if r.get("ref") == h), -1) if h else -1
            if h and new == -1:  # the indexed event left: another Primary one of that type takes its place
                prim = [(n, r["ref"]) for n, r in enumerate(refs) if r.get("role") == "Primary" and r.get("ref")]
                types = {e["handle"]: str(e.get("type")) for e in self.c.get_many("Event", [h for _, h in prim])} if prim else {}
                new = next((n for n, rh in prim if types.get(rh) == etype), -1)
            if key in person or new != -1:
                person[key] = new

    def move_event(self, op):
        """Move an event reference, citations, notes and all, from one person or family to another. Two writes:
        the new owner gets it first, then the old one loses it, so a failure in between leaves the event on both,
        never on neither. A rerun that finds that state, with the same reference on both, finishes the move. The
        role is `role` when given; otherwise it stays, except that a move between a person and a family swaps
        Primary and Family, the role each kind of owner gives its own events."""
        handle = self.resolve("Event", op["event"])
        (scls, sref), (dcls, dref) = _event_owner(op["from"]), _event_owner(op["to"])
        src, dst = self.obj(scls, sref), self.obj(dcls, dref)
        src.setdefault("_class", scls)
        dst.setdefault("_class", dcls)
        if (scls, src["handle"]) == (dcls, dst["handle"]):
            raise Conflict(f"from and to are both {scls} {self.label(src)}")
        what = f"Event {op['event']}"
        mine = [r for r in src.get("event_ref_list") or [] if r.get("ref") == handle]
        theirs = [r for r in dst.get("event_ref_list") or [] if r.get("ref") == handle]
        if not mine:
            if theirs:
                self.actions.append(f"skip: {what} is already on {dcls} {self.label(dst)} and not on {scls} {self.label(src)}")
                return
            raise Conflict(f"{scls} {self.label(src)} does not list {what}")
        if len(mine) > 1:
            raise Conflict(f"{scls} {self.label(src)} lists {what} {len(mine)} times; dedupe it first")
        ref = {k: v for k, v in mine[0].items() if k != "_class"}
        if op.get("role"):
            ref["role"] = op["role"]
        elif scls != dcls:
            ref["role"] = {"Primary": "Family", "Family": "Primary"}.get(ref.get("role"), ref.get("role"))
        if theirs:
            if [{k: v for k, v in r.items() if k != "_class"} for r in theirs] != [ref]:
                raise Conflict(f"{dcls} {self.label(dst)} already lists {what} with another reference (role "
                               f"{theirs[0].get('role')!r}); not moved")
            self._reread_found = True  # the new owner's write is in place, whichever try made it
            self.actions.append(f"{what} is already on {dcls} {self.label(dst)} with the same reference; finishing "
                                f"the move by taking it off {scls} {self.label(src)}")
        else:
            before, old = copy.deepcopy(dst), list(dst.get("event_ref_list") or [])
            dst["event_ref_list"] = [*old, {"_class": "EventRef", **ref}]
            index_key = SINGLE_VITAL.get(str((self.c.get("Event", handle) or {}).get("type")))
            if dcls == "Person" and index_key and ref.get("role") == "Primary":
                etype = next(t for t, k in SINGLE_VITAL.items() if k == index_key)
                prim = [r["ref"] for r in old if r.get("role") == "Primary" and r.get("ref")]
                for ev in self.c.get_many("Event", prim) if prim else []:
                    if str(ev.get("type")) == etype:  # the same rule as add_event: one birth and one death
                        raise Conflict(f"Person {self.label(dst)} already has a Primary {etype} (Event "
                                       f"{self.label(ev)}); merge the two with merge_event, or move that one off first")
                if dst.get(index_key, -1) == -1:  # appended last, so the indexes it has keep their events
                    dst[index_key] = len(old)
            self.save(before, dst)
        before, old = copy.deepcopy(src), list(src.get("event_ref_list") or [])
        src["event_ref_list"] = [r for r in old if r.get("ref") != handle]
        if scls == "Person":
            self._reindex(src, old)
        try:
            self.save(before, src)
        except Exception:
            self.actions.append(f"warning: {what} is now on both {scls} {self.label(src)} and {dcls} "
                                f"{self.label(dst)}: taking it off {scls} {self.label(src)} failed; a rerun "
                                "finishes the move")
            raise

    def dedupe_list(self, op):
        """Keep the first of each repeated reference in one list: the same citation twice on a person."""
        cls, ref = _parse_target(op["target"])
        target, carrier, label = self.holder(cls, ref)
        name = op["list"]
        items = carrier.get(name) or []
        seen, kept = {}, []
        for i in items:
            # the same identity as gwt-check's repeated-ref: a media ref is its media plus its region
            ident = i if isinstance(i, str) else (i.get("ref"), repr(i.get("rect")))
            first = seen.get(ident)
            if first is None:
                seen[ident] = i
                kept.append(i)
                continue
            for own in REF_OWN_LISTS if isinstance(i, dict) else ():
                if [x for x in i.get(own) or [] if x not in (first.get(own) or [])]:
                    raise Conflict(f"{label}: a repeated {name} entry {ident[0]} carries a {own} the first one "
                                   f"lacks, so dropping it would lose that")
        if len(kept) == len(items):
            self.actions.append(f"skip: {label} has no repeated entry in {name}")
            return
        before = copy.deepcopy(target)
        carrier[name] = kept
        self.save(before, target)

    def set_attribute(self, op):
        cls, ref = _parse_target(op["target"])
        if cls == "Place":
            raise Conflict("places have no attributes")
        target = self.obj(cls, ref)
        target.setdefault("_class", cls)
        attr_cls = "SrcAttribute" if cls in ("Citation", "Source") else "Attribute"
        attrs = target.get("attribute_list") or []
        before = copy.deepcopy(target)
        for a in attrs:
            if str(a.get("type")) == op["type"]:
                if a.get("value") == op["value"]:
                    self.actions.append(f"skip: {cls} {self.label(target)} already has {op['type']}={op['value']!r}")
                    return
                a["value"] = op["value"]
                break
        else:
            attrs.append({"_class": attr_cls, "type": op["type"], "value": op["value"]})
        target["attribute_list"] = attrs
        self.save(before, target)

    # -- merges -----------------------------------------------------------

    def _merge_note(self, op, cls: str, loser_label: str):
        """The note on the winner that says what was merged into it, written before the merge itself."""
        reason = f" {op['reason'].strip()}" if str(op.get("reason") or "").strip() else ""
        self.add_note(
            {
                "key": op["key"] + "#merge",
                "type": "Research",
                "text": f"Merged {loser_label} into this {cls.lower()} (ruling {op['ruling']}).{reason}",
                "attach": [{cls.lower(): op["winner"]}],
            }
        )

    def _show_merge(self, winner: dict, loser: dict) -> None:
        """The dry-run diff of a merge: what moves to the winner, and every family that points at the loser."""
        cls = winner["_class"]
        moved = []
        for lst in MERGE_LISTS:
            have = winner.get(lst) or []
            new = [x for x in loser.get(lst) or [] if x not in have]
            if new:
                moved.append(f"{lst} +{json.dumps(new, ensure_ascii=False)}")
        if cls == "Person":
            moved.insert(0, f"alternate_names +[{json.dumps(loser.get('primary_name'), ensure_ascii=False)}] (the loser's name)")
            if loser.get("gramps_id"):
                moved.append(f'attribute_list +[Merged Gramps ID = {loser["gramps_id"]}]')
        self.actions.append(f"merge {cls} {self.label(loser)} into {self.label(winner)}: " + "; ".join(moved or ["nothing to move"]))
        self.reasons.append(f"missing: merge {cls} {self.label(loser)} into {self.label(winner)}")
        if cls == "Person":
            for fh in loser.get("family_list") or []:
                fam = self.c.get("Family", fh) or {}
                role = "father" if fam.get("father_handle") == loser["handle"] else "mother"
                self.actions.append(f"  Family {self.label(fam) if fam else fh}: {role} {self.label(loser)} -> {self.label(winner)}")
            for fh in loser.get("parent_family_list") or []:
                fam = self.c.get("Family", fh) or {}
                self.actions.append(f"  Family {self.label(fam) if fam else fh}: child {self.label(loser)} -> {self.label(winner)}")
            partners = {}
            for fh in (winner.get("family_list") or []) + (loser.get("family_list") or []):
                fam = self.c.get("Family", fh) or {}
                other = {fam.get("father_handle"), fam.get("mother_handle")} - {winner["handle"], loser["handle"], None}
                key = tuple(sorted(other))
                if key in partners and partners[key][0] != fh:
                    self.actions.append(
                        f"  Family {self.label(fam) if fam else fh} merges into Family {partners[key][1]}: the same partner"
                    )
                partners.setdefault(key, (fh, self.label(fam) if fam else fh))
        else:
            for ref in loser.get("child_ref_list") or []:
                kid = self.c.get("Person", ref["ref"])
                self.actions.append(
                    f"  Person {self.label(kid) if kid else ref['ref']}: parent family {self.label(loser)} -> {self.label(winner)}"
                )
        self.actions.append(f"delete {cls} {self.label(loser)}")

    def _merge_done(self, cls: str, op) -> bool:
        """A rerun: the loser is gone and the winner carries this op's merge note."""
        ref = op["loser"]
        if ref.startswith("key:"):
            present = self.c.exists(cls, self.keymap.get(ref[4:]) or stable_handle(ref[4:]))
        else:
            present = self.c.handle_for_gramps_id(cls, ref) is not None
        if present:
            return False
        note = stable_handle(op["key"] + "#merge")
        winner = self.obj(cls, op["winner"])
        if note in (winner.get("note_list") or []):
            self.actions.append(f"skip: {cls} {op['loser']} is already merged into {self.label(winner)}")
            return True
        raise Conflict(f"no {cls} {op['loser']}, and {self.label(winner)} has no note of this merge")

    def merge_person(self, op):
        if self._merge_done("Person", op):
            return
        winner, loser = self.obj("Person", op["winner"]), self.obj("Person", op["loser"])
        for o in (winner, loser):
            o.setdefault("_class", "Person")
        name = loser.get("primary_name") or {}
        label = f"Person {self.label(loser)} ({name.get('first_name', '')} " + " ".join(
            s.get("surname", "") for s in name.get("surname_list") or []) + ")"
        self._merge_note(op, "Person", label)
        # the loser's vitals go on the winner before the merge, so a rerun after a crash finds them there
        for vital in op.get("use_loser") or []:
            idx = loser.get(USE_LOSER[vital], -1)
            if idx < 0:
                self.actions.append(f"skip: Person {self.label(loser)} has no {vital} to keep")
                continue
            ref = loser["event_ref_list"][idx]
            before = self.obj("Person", op["winner"])
            before.setdefault("_class", "Person")
            after = copy.deepcopy(before)
            refs = after["event_ref_list"] = list(after.get("event_ref_list") or [])
            if not any(r["ref"] == ref["ref"] for r in refs):
                refs.append(ref)
            after[USE_LOSER[vital]] = next(i for i, r in enumerate(refs) if r["ref"] == ref["ref"])
            self.save(before, after)
        winner = self.obj("Person", op["winner"])
        winner.setdefault("_class", "Person")
        self._show_merge(winner, loser)
        self.c.merge("Person", winner["handle"], loser["handle"], {"family_merger": True})
        self.wrote = True

    def merge_event(self, op):
        """Two events that are one fact: a death in two citations, a birth entered twice.

        Gramps' own event merge: the loser's citations, notes, media and attributes go to the winner,
        every person and family that listed the loser lists the winner, and the loser is deleted. It
        never picks a fact: a different type, date, place or description, or one the loser has and the
        winner lacks, is a conflict (correct one with update_event first). So is a person or family
        that lists both events in different roles, which Gramps would leave listing the winner twice.
        """
        if self._merge_done("Event", op):
            return
        winner, loser = self.obj("Event", op["winner"]), self.obj("Event", op["loser"])
        for o in (winner, loser):
            o.setdefault("_class", "Event")
        if winner.get("type") != loser.get("type"):
            raise Conflict(f"Event {self.label(winner)} is a {winner.get('type')}, {self.label(loser)} a {loser.get('type')}")
        for field in ("date", "place", "description"):
            w, l_ = _event_fact(winner, field), _event_fact(loser, field)
            if l_ is not None and w != l_:
                raise Conflict(
                    f"Event {self.label(winner)} and {self.label(loser)} differ in {field}"
                    + ("" if w is not None else f" ({self.label(winner)} has none, the merge would lose it)")
                    + "; correct one with update_event first"
                )
        holders = []
        for cls in ("Person", "Family"):
            for row in self.c.list_all(cls, ["event_ref_list", "gramps_id"]):
                refs = row.get("event_ref_list") or []
                lost = [repr(r.get("role")) for r in refs if r.get("ref") == loser["handle"]]
                kept = [repr(r.get("role")) for r in refs if r.get("ref") == winner["handle"]]
                if not lost:
                    continue
                if kept and set(kept) != set(lost):
                    raise Conflict(f"{cls} {self.label(row)} lists Event {self.label(winner)} and {self.label(loser)} "
                                   "in different roles; correct its event references first")
                holders.append(f"  {cls} {self.label(row)}: " + (
                    f"already lists {self.label(winner)}, drops {self.label(loser)}" if kept
                    else f"lists {self.label(winner)} instead of {self.label(loser)}"))
        self._merge_note(op, "Event", f"Event {self.label(loser)}")
        winner = self.obj("Event", op["winner"])
        winner.setdefault("_class", "Event")
        self._show_merge(winner, loser)
        delete = self.actions.pop()
        self.actions += holders + [delete]
        self.c.merge("Event", winner["handle"], loser["handle"])
        self.wrote = True

    def merge_family(self, op):
        if self._merge_done("Family", op):
            return
        winner, loser = self.obj("Family", op["winner"]), self.obj("Family", op["loser"])
        body = {}
        for role in ("father", "mother"):
            w, l_ = winner.get(f"{role}_handle"), loser.get(f"{role}_handle")
            if w and l_ and w != l_:
                raise Conflict(
                    f"Family {self.label(winner)} and {self.label(loser)} have different {role}s; "
                    "merge those two people first with merge_person"
                )
            if l_ and not w:
                body[f"phoenix_{role}_handle"] = l_  # keep the only one there is
        self._merge_note(op, "Family", f"Family {self.label(loser)}")
        winner = self.obj("Family", op["winner"])
        winner.setdefault("_class", "Family")
        loser.setdefault("_class", "Family")
        self._show_merge(winner, loser)
        self.c.merge("Family", winner["handle"], loser["handle"], body)
        self.wrote = True
