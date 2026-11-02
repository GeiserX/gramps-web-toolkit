"""The lint framework: the list, the tree it will be applied to, rules, findings and the --prove run.

A rule is a subclass of Rule in a module named in RULE_MODULES. It has a `name`, its finding `codes`, and two
methods:

    check(ctx)  -> list of Finding; raise NothingToCheck(reason) when it could not look at anything
    mutate(ctx) -> Mutant: a broken copy of ctx.ops and the code the rule must fire on it, built from the
                   run's own settings; None when no mutant can be built from them

`prove()` runs each rule on the list and on its own mutant, under the same settings, and passes only when the
rule could look and the expected code fires more often on the mutant than on the list. So a rule that has
nothing to check under your settings, fails for another reason, or fires anyway, proves nothing.
"""
from __future__ import annotations

import copy
import gzip
import importlib
import json
import os
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from gwt_apply.ops import validate
from gwt_core.rules import DateError, Index, read_date

RULE_MODULES = ["gwt_lint.rules_tree"]

CLASSES = {"people": "Person", "families": "Family", "events": "Event", "places": "Place",
           "citations": "Citation", "sources": "Source", "media": "Media", "notes": "Note",
           "repositories": "Repository", "tags": "Tag"}
KINDS = {"person": "Person", "family": "Family", "event": "Event", "citation": "Citation", "source": "Source",
         "media": "Media", "place": "Place", "repository": "Repository", "note": "Note",
         "child_ref": "ChildRef", "association": "Association"}
# what each add_* op makes; link_child with `probable` and unlink_child also make a note
CREATES = {"add_person": "Person", "add_event": "Event", "add_family": "Family", "add_place": "Place",
           "add_source": "Source", "add_repository": "Repository", "add_citation": "Citation",
           "add_media": "Media", "add_note": "Note", "add_transcription": "Note"}
MERGED = {"merge_person": "Person", "merge_family": "Family", "merge_event": "Event",
          "merge_repository": "Repository"}
# plain fields that name one object (a Gramps ID or key:<key>), by the kind they name. namesake_of and
# new_couple_of are left out: they name the people and families the new object is NOT, so the op does not touch them
FIELDS = {"person": "Person", "child": "Person", "father": "Person", "mother": "Person", "family": "Family",
          "event": "Event", "citation": "Citation", "note": "Note", "place": "Place", "source": "Source",
          "repository": "Repository", "enclosed_by": "Place", "children": "Person"}
# fields whose value is new free text, per op ("set.text" is the text key inside `set`)
TEXT_FIELDS = {"add_note": ("text",), "add_transcription": ("text", "margin_reading"),
               "add_event": ("description",), "update_note": ("set.text",), "update_event": ("set.description",),
               "link_child": ("probable_reason",), "unlink_child": ("reason",), "add_person": ("namesake_reason",),
               "add_family": ("new_couple_reason",), "update_child_ref": ("confirm_probable",),
               "set_attribute": ("value",), "merge_person": ("reason",), "merge_family": ("reason",),
               "merge_event": ("reason",), "merge_repository": ("reason",), "add_citation": ("page",),
               "update_citation": ("set.page",), "add_media": ("desc",), "add_source": ("title", "author", "pubinfo"),
               "update_source": ("set.title",)}
# fields of gwt-apply ops that are a name, an ID, a type or a path, not free text; a test checks that every field
# gwt-apply takes is in TEXT_FIELDS or here, so a field it adds later cannot slip past the text rules unseen
LABELS = {"first_name", "surname", "gender", "gramps_id", "person", "event", "place", "note", "citation", "source",
          "family", "child", "father", "mother", "repository", "winner", "loser", "enclosed_by", "date", "role", "type",
          "frel", "mrel", "name", "abbrev", "url", "call_number", "media_type", "file", "path", "mime", "list",
          "relation"}
PROBABLE = "[probable]"


class NothingToCheck(Exception):
    """The rule could not look at anything: a missing setting, a root the tree does not have."""


class InputError(Exception):
    """The list, the snapshot or the config cannot be read."""


@dataclass
class Finding:
    rule: str
    code: str
    key: str
    line: int
    message: str
    fix: str = ""

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if v != ""}


@dataclass
class Mutant:
    ops: list
    code: str


class Rule:
    name = ""
    codes: dict[str, str] = {}

    def check(self, ctx) -> list[Finding]:
        raise NotImplementedError

    def mutate(self, ctx) -> Mutant | None:
        raise NotImplementedError

    def finding(self, code, op, message, fix=""):
        assert code in self.codes, code
        return Finding(self.name, code, str(op.get("key", "")), op.get("_line", 0), message, fix)


def registry() -> dict[str, Rule]:
    rules = {}
    for mod in RULE_MODULES:
        for r in importlib.import_module(mod).RULES:
            if r.name in rules:
                raise ValueError(f"two rules named {r.name!r}")
            rules[r.name] = r()
    return rules


# --- inputs --------------------------------------------------------------------------------------------------

def _read_json(path):
    p = Path(path)
    try:
        raw = p.read_bytes()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw)
    except (OSError, ValueError, EOFError, zlib.error) as e:  # ValueError covers bad JSON and bad UTF-8
        raise InputError(f"{path}: {e}") from None


def read_list(path, check=True) -> list[dict]:
    """The ops of a change list in any shape gwt-apply reads (JSON Lines, a JSON array, or {"ops": [...]}),
    each with `_line`: its line in JSON Lines, its place in the array otherwise. A list gwt-apply would refuse
    is an InputError, so a malformed list can never lint clean; `check=False` (other open lists, still being
    written) skips that."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"{path}: {e}") from None
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, (list, dict)):
        ops = data["ops"] if isinstance(data, dict) and "ops" in data else [data] if isinstance(data, dict) else data
        if not isinstance(ops, list):
            raise InputError(f"{path}: ops is not a list")
        numbered = list(enumerate(ops, 1))
    else:
        numbered = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                numbered.append((n, json.loads(line)))
            except ValueError as e:
                raise InputError(f"{path}:{n}: not JSON: {e}") from None
    for n, op in numbered:
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            raise InputError(f"{path}:{n}: not an op (an object with an `op` field)")
    errors = validate([op for _, op in numbered]) if check else []
    if errors:
        raise InputError(f"{path}: gwt-apply would refuse this list:\n  " + "\n  ".join(errors))
    for n, op in numbered:
        op["_line"] = n
    return [op for _, op in numbered]


def read_lists(folder, skip=None) -> dict[str, list[dict]]:
    """Every *.jsonl in a folder (not below it), by file name; `skip` is a path left out (the list itself)."""
    d = Path(folder)
    if not d.is_dir():
        raise NothingToCheck(f"no folder {folder}")
    skip = os.path.realpath(skip) if skip else None
    return {p.name: read_list(p, check=False) for p in sorted(d.glob("*.jsonl")) if os.path.realpath(p) != skip}


def text_of(v) -> str:
    """A Gramps type, role or StyledText as plain text."""
    if isinstance(v, dict):
        return v.get("string") or ""
    return v or ""


class Tree:
    """A snapshot (gwt-check dump or gwt-apply snapshot), by class, handle and Gramps ID."""

    def __init__(self, data: dict):
        objs = data.get("objects") if isinstance(data.get("objects"), dict) else data
        self.handles: dict[str, dict] = {}
        self.gids: dict[str, dict] = {}
        for name, cls in CLASSES.items():
            rows = objs.get(cls) if objs.get(cls) is not None else objs.get(name) or []
            rows = list(rows.values()) if isinstance(rows, dict) else rows
            self.handles[cls] = {o["handle"]: o for o in rows if isinstance(o, dict) and o.get("handle")}
            self.gids[cls] = {o["gramps_id"]: o["handle"] for o in self.handles[cls].values() if o.get("gramps_id")}
        self.tag_names = {h: t.get("name") or "" for h, t in self.handles["Tag"].items()}

    @classmethod
    def load(cls, path):
        data = _read_json(path)
        if not isinstance(data, dict):
            raise InputError(f"{path}: not a snapshot")
        return cls(data)


class View:
    """The tree as the list will leave it, for what rules ask about people: the people, families and events
    the list adds (by `key:<key>` handles) and the links it adds, laid over the snapshot. Removals are not
    laid over, so a rule judges an op on the tree before it."""

    def __init__(self, tree: Tree, ops: list[dict]):
        self.tree = tree
        self.people = dict(tree.handles["Person"])
        self.families = dict(tree.handles["Family"])
        self.events = dict(tree.handles["Event"])
        self.keys = {f"key:{op['key']}": CREATES[op["op"]] for op in ops if op["op"] in CREATES and op.get("key")}
        self._copied: set[str] = set()
        for op in ops:
            self._lay(op)
        self.index = Index({"people": self.people, "families": self.families, "events": self.events})
        self._owners = None

    def resolve(self, ref, cls) -> str | None:
        """The handle of a Gramps ID or key:<key> of that class, None when neither the tree nor the list has it."""
        if not isinstance(ref, str):
            return None
        if ref.startswith("key:"):
            return ref if self.keys.get(ref) == cls else None
        return self.tree.gids.get(cls, {}).get(ref)

    def gid(self, handle) -> str:
        for m in (self.people, self.families, self.events):
            if handle in m:
                return m[handle].get("gramps_id") or handle
        return handle or "?"

    def label(self, handle) -> str:
        p = self.people.get(handle)
        if not p:
            return self.gid(handle)
        n = p.get("primary_name") or {}
        sur = " ".join(s.get("surname") or "" for s in n.get("surname_list") or []).strip()
        name = " ".join(x for x in (n.get("first_name") or "", sur) if x)
        return f"{self.gid(handle)} ({name})" if name else self.gid(handle)

    def _own(self, m, h):
        if h not in self._copied:
            m[h] = copy.deepcopy(m[h])
            self._copied.add(h)
        return m[h]

    def _lay(self, op):
        kind, key = op["op"], f"key:{op.get('key')}"
        if kind == "add_person":
            self.people[key] = {"handle": key, "gramps_id": key, "event_ref_list": [], "family_list": [],
                                "parent_family_list": [], "person_ref_list": [], "tag_list": [],
                                "primary_name": {"first_name": op.get("first_name") or "",
                                                 "surname_list": [{"surname": op.get("surname") or ""}]}}
            self._copied.add(key)
        elif kind == "add_family":
            fam = {"handle": key, "gramps_id": key, "child_ref_list": [], "event_ref_list": [], "tag_list": [],
                   "father_handle": self.resolve(op.get("father"), "Person"),
                   "mother_handle": self.resolve(op.get("mother"), "Person")}
            self.families[key] = fam
            self._copied.add(key)
            for h in (fam["father_handle"], fam["mother_handle"]):
                if h in self.people:
                    self._own(self.people, h).setdefault("family_list", []).append(key)
            for c in op.get("children") or []:
                self._link(key, self.resolve(c, "Person"), "Birth", "Birth")
        elif kind == "add_event":
            try:
                date = read_date(op.get("date"))
            except DateError:
                date = read_date(None)
            self.events[key] = {"handle": key, "gramps_id": key, "type": op.get("type") or "", "date": date,
                                "place": self.resolve(op.get("place"), "Place")}
            self._copied.add(key)
            owner = self.resolve(op.get("person"), "Person")
            if owner in self.people:
                self._own(self.people, owner).setdefault("event_ref_list", []).append(
                    {"ref": key, "role": op.get("role") or "Primary"})
            fam = self.resolve(op.get("family"), "Family")
            if fam in self.families:
                self._own(self.families, fam).setdefault("event_ref_list", []).append({"ref": key, "role": "Family"})
        elif kind == "link_child":
            self._link(self.resolve(op.get("family"), "Family"), self.resolve(op.get("child"), "Person"),
                       op.get("frel") or "Birth", op.get("mrel") or "Birth")
        elif kind == "add_association":
            p, t = self.resolve(op.get("person"), "Person"), self.resolve(op.get("target"), "Person")
            if p in self.people and t:
                self._own(self.people, p).setdefault("person_ref_list", []).append(
                    {"ref": t, "rel": op.get("relation") or ""})

    def _link(self, fam, child, frel, mrel):
        if fam not in self.families or child not in self.people:
            return
        self._own(self.families, fam).setdefault("child_ref_list", []).append(
            {"ref": child, "frel": frel, "mrel": mrel})
        self._own(self.people, child).setdefault("parent_family_list", []).append(fam)

    def spouses(self, fam) -> list[str]:
        f = self.families.get(fam) or {}
        return [h for h in (f.get("father_handle"), f.get("mother_handle")) if h in self.people]

    def event_owners(self, ev) -> tuple[list[str], list[str]]:
        """(people, families) whose event list holds this event."""
        if self._owners is None:
            self._owners = {}
            for kind, m in (("p", self.people), ("f", self.families)):
                for h, o in m.items():
                    for r in o.get("event_ref_list") or []:
                        self._owners.setdefault(r.get("ref"), ([], []))[kind == "f"].append(h)
        return self._owners.get(ev, ([], []))


# --- what an op names ----------------------------------------------------------------------------------------

def refs(op) -> list[tuple[str, object]]:
    """(class, ref) for every object the op names: ChildRef refs are (family, child), Association refs
    (person, target). The object an add_* op makes is included as its key:<key>."""
    out = []
    kind = op["op"]
    if kind in CREATES and op.get("key"):
        out.append((CREATES[kind], f"key:{op['key']}"))
    for f, cls in FIELDS.items():
        v = op.get(f)
        for r in (v if isinstance(v, list) else [v]):
            if isinstance(r, str):
                out.append((cls, r))
    if kind in MERGED:
        out += [(MERGED[kind], op.get(f)) for f in ("winner", "loser") if isinstance(op.get(f), str)]
    if kind == "add_association" and isinstance(op.get("target"), str):
        out.append(("Person", op["target"]))
    s = op.get("set") if isinstance(op.get("set"), dict) else {}
    if isinstance(s.get("place"), str):
        out.append(("Place", s["place"]))
    return out + _dict_refs(list(op.get("attach") or []) + [op.get(f) for f in ("target", "object", "from", "to")])


def attach_targets(op) -> list[tuple[str, object]]:
    """(class, ref) of what the op hangs something on: its `attach` list, a dict `target` and a `to`."""
    return _dict_refs(list(op.get("attach") or []) + [op.get("target"), op.get("to")])


def _dict_refs(items) -> list[tuple[str, object]]:
    out = []
    for t in items:
        if isinstance(t, dict) and len(t) == 1:
            (k, v), = t.items()
            cls = KINDS.get(k)
            if cls == "ChildRef" and isinstance(v, dict):
                out.append((cls, (v.get("family"), v.get("child"))))
            elif cls == "Association" and isinstance(v, dict):
                out.append((cls, (v.get("person"), v.get("target"))))
            elif cls and isinstance(v, str):
                out.append((cls, v))
    return out


def texts(op) -> list[str]:
    """The new free text an op writes."""
    out = []
    for f in TEXT_FIELDS.get(op["op"], ()):
        if f.startswith("set."):
            v = (op.get("set") or {}).get(f[4:]) if isinstance(op.get("set"), dict) else None
        else:
            v = op.get(f)
        if isinstance(v, str) and v.strip():
            out.append(v)
    return out


def creates(op) -> bool:
    """Whether the op makes an object, the only thing its own `tag` lands on."""
    return op["op"] in CREATES or op["op"] == "unlink_child" or (op["op"] == "link_child" and bool(op.get("probable")))


def tags_of(op) -> list[str]:
    t = op.get("tag")
    return [t] if isinstance(t, str) else [x for x in t if isinstance(x, str)] if isinstance(t, list) else []


# --- running -------------------------------------------------------------------------------------------------

class Context:
    """One lint run's inputs. `view` is built on first use, for these ops."""

    def __init__(self, ops, tree, config=None, path=None, records=None):
        self.ops, self.tree, self.config = ops, tree, config or {}
        self.path, self.records = path, records
        self._view = None

    @property
    def view(self) -> View:
        if self._view is None:
            self._view = View(self.tree, self.ops)
        return self._view

    def section(self, name) -> dict:
        s = self.config.get(name)
        return s if isinstance(s, dict) else {}

    def open_lists(self, folder) -> dict[str, list[dict]]:
        """The other open lists, the list itself left out."""
        return read_lists(folder, skip=self.path)

    def but(self, ops):
        return Context(ops, self.tree, self.config, self.path, self.records)


@dataclass
class Result:
    rule: str
    status: str            # ok, findings, nothing
    findings: list = field(default_factory=list)
    reason: str = ""
    info: list = field(default_factory=list)

    def as_dict(self):
        return {"rule": self.rule, "status": self.status, "reason": self.reason, "info": self.info,
                "findings": [f.as_dict() for f in self.findings]}


def run_rule(rule, ctx) -> Result:
    try:
        out = rule.check(ctx)
    except NothingToCheck as e:
        return Result(rule.name, "nothing", reason=str(e))
    findings = [f for f in out if isinstance(f, Finding)]
    info = [x for x in out if isinstance(x, str)]
    return Result(rule.name, "findings" if findings else "ok", findings, info=info)


def exit_code(results) -> int:
    """2 when any rule had nothing to check (it wins: a run where one rule did not look is no result), else 1
    when there is a finding, else 0."""
    if any(r.status == "nothing" for r in results):
        return 2
    return 1 if any(r.findings for r in results) else 0


def _count(result, code) -> int:
    return sum(f.code == code for f in result.findings)


def prove(rule, ctx) -> tuple[bool, str]:
    """Run the rule on the list and on its own mutant, both under the run's own settings: (passed, one line).
    It passes when the rule could look and the mutant fires the expected code more often than the list."""
    before = run_rule(rule, ctx)
    if before.status == "nothing":
        return False, f"{rule.name}: nothing to check under these settings: {before.reason}"
    m = rule.mutate(ctx)
    if m is None:
        return False, f"{rule.name}: no mutant can be built from this tree and these settings"
    after = run_rule(rule, ctx.but(m.ops))
    if after.status == "nothing":
        return False, f"{rule.name}: had nothing to check on its own mutant: {after.reason}"
    b, a = _count(before, m.code), _count(after, m.code)
    if a <= b:
        got = sorted({f.code for f in after.findings}) or ["nothing"]
        return False, f"{rule.name}: its mutant did not fire {m.code} ({b} on the list, {a} on the mutant; fired {', '.join(got)})"
    return True, f"{rule.name}: {m.code} fired on its mutant ({b} on the list, {a} on the mutant)"
