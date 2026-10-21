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
}

# op -> (required fields, optional fields), on top of op/key/ruling
SPECS: dict[str, tuple[set[str], set[str]]] = {
    "add_person": (
        {"surname", "first_name"},
        {"gender", "gramps_id", "allow_namesake", "namesake_reason", "namesake_of"},
    ),
    "update_person": ({"person", "set"}, set()),
    "update_event": ({"event", "set"}, set()),
    "add_event": ({"type"}, {"person", "family", "role", "date", "place", "description", "gramps_id"}),
    "add_family": (set(), {"father", "mother", "children", "type", "gramps_id"}),
    "link_child": ({"family", "child"}, {"frel", "mrel", "probable", "probable_reason"}),
    "add_place": ({"name", "type"}, {"enclosed_by", "lat", "long", "gramps_id"}),
    "add_source": ({"title"}, {"author", "pubinfo", "abbrev", "gramps_id"}),
    "add_citation": ({"source", "page", "attach"}, {"confidence", "date", "gramps_id"}),
    "add_media": ({"file", "path", "attach"}, {"desc", "mime", "gramps_id"}),
    "add_note": ({"text", "attach"}, {"type", "gramps_id"}),
    "set_attribute": ({"target", "type", "value"}, set()),
    "merge_person": ({"winner", "loser"}, {"use_loser", "reason"}),
    "merge_family": ({"winner", "loser"}, {"reason"}),
}
MERGE_LISTS = [  # what Gramps' merge carries from the loser to the winner, list by list
    "alternate_names", "event_ref_list", "citation_list", "note_list", "media_list", "attribute_list",
    "address_list", "urls", "person_ref_list", "lds_ord_list", "tag_list", "parent_family_list", "family_list",
    "child_ref_list",
]
USE_LOSER = {"birth": "birth_ref_index", "death": "death_ref_index"}
PERSON_FIELDS = {"first_name", "surname", "gender"}
EVENT_FIELDS = {"type", "date", "place", "description"}
GENDER = {"F": 0, "M": 1, "U": 2}
SINGLE_VITAL = {"Birth": "birth_ref_index", "Death": "death_ref_index"}
PROBABLE = "[probable]"  # how a doubtful parent link is marked: a note on its child_ref starting with this
# what each target kind can carry
CARRIES = {
    "citation_list": {"Person", "Family", "Event", "Media", "Place"},
    "media_list": {"Person", "Family", "Event", "Citation", "Source", "Place"},
    "note_list": {"Person", "Family", "Event", "Citation", "Source", "Media", "Place", "ChildRef"},
}
CARRIES["citation_list"] = CARRIES["citation_list"] | {"ChildRef"}


def fold(text: str) -> str:
    """Compare names without case, accents or extra spaces: "Agüera" == "aguera"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(bare.casefold().split())


_GRAMPS_ID = re.compile(r"^[A-Za-z]+\d+$")


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
    """{"person": "I1"} -> ("Person", "I1"); {"child_ref": {"family": F, "child": I}} -> ("ChildRef", (F, I))."""
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"a target is one of {sorted(TARGET_KINDS) + ['child_ref']} -> ref, got {spec!r}")
    (kind, ref), = spec.items()
    if kind == "child_ref":
        if not isinstance(ref, dict) or set(ref) != {"family", "child"}:
            raise ValueError(f"a child_ref target is {{\"family\": ..., \"child\": ...}}, got {ref!r}")
        return "ChildRef", (ref["family"], ref["child"])
    if kind not in TARGET_KINDS or not isinstance(ref, str) or not ref:
        raise ValueError(f"bad target {spec!r}")
    return TARGET_KINDS[kind], ref


def _target_refs(spec) -> list:
    if not isinstance(spec, dict) or len(spec) != 1:
        return []
    value = next(iter(spec.values()))
    return list(value.values()) if isinstance(value, dict) else [value]


def _ref_list(value) -> list:
    return value if isinstance(value, list) else [value]


def validate(ops: list[dict]) -> list[str]:
    """Every problem with the change list, found before anything is read or written."""
    errors = []
    keys: set[str] = set()
    payloads: dict[str, str] = {}  # the op without key and ruling -> where it was first seen
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
        allowed = required | optional | {"op", "key", "ruling"}
        missing = [f for f in required if op.get(f) in (None, "", [])]
        if name == "add_citation" and op.get("page") == "":
            missing.remove("page")  # an empty page is a real value
        if missing:
            errors.append(f"{where}: missing {sorted(missing)}")
        extra = set(op) - allowed
        if extra:
            errors.append(f"{where}: unknown fields {sorted(extra)}")
        payload = json.dumps({k: v for k, v in op.items() if k not in ("key", "ruling")}, sort_keys=True)
        if payload in payloads:
            errors.append(f"{where}: the same change as {payloads[payload]}; one change, one op")
        else:
            payloads[payload] = where
        refs = [op.get(f) for f in ("person", "family", "father", "mother", "child", "source", "place", "enclosed_by",
                                    "winner", "loser")]
        refs += list(op.get("children") or [])
        refs += _ref_list(op.get("namesake_of")) if op.get("namesake_of") else []
        for t in [op.get("target"), *(op.get("attach") or [])]:
            refs += _target_refs(t)
        for ref in refs:
            if ref is not None and not (isinstance(ref, str) and (ref.startswith("key:") or _GRAMPS_ID.match(ref))):
                errors.append(f"{where}: {ref!r} is neither key:<key> nor a Gramps ID (never a handle)")
        try:
            if "date" in op:
                parse_date(op["date"])
            if "gender" in op and op["gender"] not in GENDER:
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
            if name == "link_child":
                if op.get("probable", True) is not True:
                    raise ValueError("probable is true or absent")
                if bool(op.get("probable")) != bool(str(op.get("probable_reason") or "").strip()):
                    raise ValueError("probable: true and probable_reason go together")
            if name.startswith("merge_") and op.get("winner") == op.get("loser"):
                raise ValueError("winner and loser are the same")
            if name == "merge_person" and set(op.get("use_loser") or []) - set(USE_LOSER):
                raise ValueError(f"use_loser takes {sorted(USE_LOSER)}")
            if name == "add_event" and bool(op.get("person")) == bool(op.get("family")):
                raise ValueError("add_event needs exactly one of person or family")
            if name == "add_family" and not (op.get("father") or op.get("mother")):
                raise ValueError("add_family needs a father or a mother")
            if name == "update_person":
                s = op.get("set")
                if not isinstance(s, dict) or not s or set(s) - PERSON_FIELDS:
                    raise ValueError(f"set takes only {sorted(PERSON_FIELDS)}")
                if "gender" in s and s["gender"] not in GENDER:
                    raise ValueError(f"gender is one of {sorted(GENDER)}")
            if name == "update_event":
                s = op.get("set")
                if not isinstance(s, dict) or not s or set(s) - EVENT_FIELDS:
                    raise ValueError(f"set takes only {sorted(EVENT_FIELDS)}")
                if "date" in s:
                    parse_date(s["date"])
                if s.get("place") and not (s["place"].startswith("key:") or _GRAMPS_ID.match(s["place"])):
                    raise ValueError(f"{s['place']!r} is neither key:<key> nor a Gramps ID (never a handle)")
            if name == "set_attribute" and _parse_target(op.get("target"))[0] == "ChildRef":
                raise ValueError("a child_ref has no attributes")
            if name == "add_place":
                if ("lat" in op) != ("long" in op):
                    raise ValueError("lat and long go together")
                if "lat" in op and not (-90 <= float(op["lat"]) <= 90 and -180 <= float(op["long"]) <= 180):
                    raise ValueError("lat/long out of range")
            for spec in op.get("attach") or []:
                cls, _ = _parse_target(spec)
                lst = {"add_citation": "citation_list", "add_media": "media_list", "add_note": "note_list"}[name]
                if cls not in CARRIES[lst]:
                    raise ValueError(f"a {cls} cannot carry a {name[4:]}")
            if name == "add_media" and not Path(op.get("file", "")).is_file():
                raise ValueError(f"file not found: {op.get('file')}")
            if name == "add_media" and Path(op.get("path", "")).is_absolute():
                raise ValueError("path is relative to the tree's media folder")
        except (ValueError, DateError) as exc:
            errors.append(f"{where}: {exc}")
    return errors


@dataclass
class Result:
    key: str
    op: str
    ruling: str
    status: str = "done"  # done | skip | conflict | error
    actions: list[str] = field(default_factory=list)




class Applier:
    def __init__(self, client: Client, *, retries: int = 5, retry_wait: float = 30.0, lock=None):
        self.c = client
        self.lock = lock  # a TreeLock to keep fresh before every try, during --apply
        self.retries = retries
        self.retry_wait = retry_wait
        self.keymap: dict[str, str] = {}  # key -> handle of the object it ended up as
        self.actions: list[str] = []
        self.wrote = False
        self._names: dict[tuple[str, str], str] | None = None  # (first name, surname) -> Gramps ID

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
        if handle is None:
            raise Conflict(f"no {cls} with Gramps ID {ref}")
        return handle

    def obj(self, cls: str, ref: str) -> dict:
        obj = self.c.get(cls, self.resolve(cls, ref))
        assert obj is not None
        return obj

    @staticmethod
    def label(obj: dict) -> str:
        return obj.get("gramps_id") or obj["handle"]

    # -- writes, each one recorded as an action ---------------------------

    def create(self, obj: dict, extra: str = "") -> None:
        self.actions.append(f"create {obj['_class']}{extra}: {json.dumps(obj, ensure_ascii=False)}")
        self.c.create(obj)
        self.wrote = True

    def save(self, before: dict, after: dict) -> None:
        changes = []
        for k in sorted(set(before) | set(after)):
            if k.startswith("_") or before.get(k) == after.get(k):
                continue
            changes.append(
                f"{k}: {json.dumps(before.get(k), ensure_ascii=False)} -> "
                f"{json.dumps(after.get(k), ensure_ascii=False)}"
            )
        self.actions.append(f"update {after['_class']} {self.label(after)}: " + "; ".join(changes))
        self.c.update(after)
        self.wrote = True

    def holder(self, cls: str, ref) -> tuple[dict, dict, str]:
        """(object to PUT, dict that carries the lists, label). A child_ref lives inside its family."""
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
        for op in ops:
            res = Result(op["key"], op["op"], op["ruling"])
            self.c.context = {"key": op["key"], "op": op["op"], "ruling": op["ruling"]}
            for attempt in range(1, self.retries + 1):
                if self.lock is not None:  # every try: a slow server can make one op outlast the lock
                    self.lock.refresh()
                self.actions, self.wrote = [], False
                try:
                    getattr(self, op["op"])(op)
                    res.status = "done" if self.wrote else "skip"
                    break
                except Retryable as exc:
                    if attempt == self.retries:
                        res.status = "error"
                        self.actions.append(f"error: {exc} (gave up after {attempt} tries)")
                        break
                    self.c.log(event="retry", attempt=attempt, error=str(exc))
                    time.sleep(self.retry_wait)
                except Conflict as exc:
                    res.status = "conflict"
                    self.actions.append(f"conflict: {exc}")
                    break
                except ApiError as exc:
                    res.status = "error"
                    self.actions.append(f"error: {exc}")
                    break
            res.actions = self.actions
            self.c.log(event="op", status=res.status, actions=res.actions)
            results.append(res)
            if res.status in ("conflict", "error"):
                break
        self.c.context = {}
        return results

    def _existing(self, cls: str, op: dict) -> dict | None:
        """The object an earlier run made for this key, if any."""
        handle = stable_handle(op["key"])
        obj = self.c.get(cls, handle) if self.c.exists(cls, handle) else None
        if obj is not None:
            self.keymap[op["key"]] = handle
            self.actions.append(f"skip: {cls} {self.label(obj)} already made by key {op['key']!r}")
        return obj

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
        unchecked = [x for x in same if x not in _ref_list(op.get("namesake_of") or [])]
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
            for ev in events:
                if str(ev.get("type")).lower() != etype.lower():
                    continue
                if date_key(ev.get("date")) == date_key(date):
                    self.keymap[op["key"]] = ev["handle"]
                    self.actions.append(
                        f"skip: {target_cls} {self.label(target)} already has {etype} "
                        f"{op.get('date') or '(no date)'} as Event {self.label(ev)}"
                    )
                    return
                if etype in SINGLE_VITAL and role == "Primary" and roles.get(ev["handle"]) == "Primary":
                    raise Conflict(
                        f"{target_cls} {self.label(target)} already has a {etype} with another date "
                        f"(Event {self.label(ev)}); fix that event instead of adding a second one"
                    )
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
        for row in self.c.list_all("Source", ["title", "gramps_id"]):
            if (row.get("title") or "").strip() == title:
                self.keymap[op["key"]] = row["handle"]
                self.actions.append(f"skip: Source {row.get('gramps_id') or row['handle']} has that title")
                return
        self._check_gramps_id("Source", op)
        h = stable_handle(op["key"])
        obj = {"_class": "Source", "handle": h, "gramps_id": op.get("gramps_id", ""), "title": title}
        for f in ("author", "pubinfo", "abbrev"):
            if op.get(f):
                obj[f] = op[f]
        self.create(obj)
        self.keymap[op["key"]] = h

    def add_citation(self, op):
        source = self.resolve("Source", op["source"])
        page = op["page"].strip()
        cit = self._existing("Citation", op)
        handle = cit["handle"] if cit else None
        if handle is None:
            for row in self.c.list_all("Citation", ["source_handle", "page", "gramps_id"]):
                if row.get("source_handle") == source and (row.get("page") or "").strip() == page:
                    handle = row["handle"]
                    self.keymap[op["key"]] = handle
                    self.actions.append(
                        f"skip: Citation {row.get('gramps_id') or handle} already cites that page"
                    )
                    break
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
                for n in self.c.get_many("Note", carrier.get("note_list") or []):
                    found = (n.get("text") or {}).get("string") or ""
                    if found == text or (marker and found.startswith(marker)):
                        handle = n["handle"]
                        self.keymap[op["key"]] = handle
                        self.actions.append(f"skip: {label} already has that text as Note {self.label(n)}")
                        break
                if handle:
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
