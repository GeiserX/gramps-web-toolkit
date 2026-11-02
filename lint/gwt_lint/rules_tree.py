"""Tree rules: what a change list may do to which people, read from the list and a snapshot only.

    scope    every person and family an op touches is a blood relative of a root, or the spouse of one
    protect  no new events, dates, places, links or attachments on protected people, and no new text that
             names one of them near a life-event word
    living   no exact date, place, quote or marriage on a person who may be living; no identity number in
             any new text
    overlap  no object another open list touches; no update repeated from an applied list
    weak     an op that makes a weak claim carries the weak tag; a tag on an op that makes nothing is inert
    count    no new text that counts what the tree holds («the tree has 40 people named ...»)
"""
from __future__ import annotations

import json
import re
from collections import deque

from gwt_core.rules import LIVING_YEARS, LOCALES, DateError, find_ids, fold, load_locale, possibly_living, read_date

from .framework import (MERGED, PROBABLE, Mutant, NothingToCheck, Rule, attach_targets, creates, refs, tags_of,
                        text_of, texts)

UPDATED = {"update_person": "Person", "update_family": "Family"}  # update_event has its own codes
LINEAGE = {"", "Birth", "Unknown", "Adopted"}  # parent links that carry blood; Stepchild, Foster, Sponsored do not
QUOTE = re.compile(r"«[^»]+»|“[^”]+”|\"[^\"]+\"")
# The word "tree" in each locale of gwt_core; [count] words in lint.toml replaces it
TREE_WORDS = ["tree", "árbol", "arbre", "albero", "árvore", "stammbaum", "baum", "stamboom", "zuhaitz", "árbore",
              "arbor"]


def _first_gid(ctx):
    return min(ctx.tree.gids["Person"]) if ctx.tree.gids["Person"] else None


def _people(view, cls, ref, families_as_spouses=True):
    """(person handles, family handles, unknown) one reference touches. An event touches its owners; a
    child_ref its child and family; an association its person. Places, sources, notes, citations and media
    are not people: they touch nobody. `unknown` is the ref when neither the tree nor the list has it."""
    if cls == "ChildRef":
        fam, child = ref
        p, f, u = _people(view, "Person", child)
        p2, f2, u2 = _people(view, "Family", fam, families_as_spouses)
        return p + p2, f + f2, u or u2
    if cls == "Association":
        return _people(view, "Person", ref[0])
    if cls not in ("Person", "Family", "Event"):
        return [], [], None
    h = view.resolve(ref, cls)
    if h is None:
        return [], [], ref
    if cls == "Person":
        return [h], [], None
    if cls == "Family":
        return (view.spouses(h) if families_as_spouses else []), [h], None
    people, fams = view.event_owners(h)
    return list(people) + [s for f in fams for s in view.spouses(f)], list(fams), None


def _exact(text) -> bool:
    try:
        d = read_date(text)
    except DateError:
        return False
    return bool(d["dateval"][0])


class Scope(Rule):
    name = "scope"
    codes = {"scope-out": "touches a person or family outside the roots' blood relatives and their spouses",
             "scope-unknown": "names a person, family or event that neither the snapshot nor the list has"}

    @staticmethod
    def reach(view, roots) -> tuple[dict, set]:
        """({person handle: path from a root}, family handles in scope). Blood: the roots' ancestors and every
        descendant of those, through birth, adoptive or unknown parent links only; then their spouses."""
        def kids_link(fam, h, cr):
            f = view.families.get(fam) or {}
            return text_of(cr.get("frel") if h == f.get("father_handle") else cr.get("mrel"))

        anc, q = {}, deque((r, view.gid(r)) for r in roots)
        while q:
            h, path = q.popleft()
            if h in anc or h not in view.people:
                continue
            anc[h] = path
            for fam in view.people[h].get("parent_family_list") or []:
                f = view.families.get(fam) or {}
                cr = next((c for c in f.get("child_ref_list") or [] if c.get("ref") == h), {})
                for side, rel in (("father", "frel"), ("mother", "mrel")):
                    p = f.get(side + "_handle")
                    if p and text_of(cr.get(rel)) in LINEAGE:
                        q.append((p, f"{path} > {side} {view.gid(p)}"))
        blood, q = {}, deque(anc.items())
        while q:
            h, path = q.popleft()
            if h in blood or h not in view.people:
                continue
            blood[h] = path
            for fam in view.people[h].get("family_list") or []:
                for cr in (view.families.get(fam) or {}).get("child_ref_list") or []:
                    if kids_link(fam, h, cr) in LINEAGE and cr.get("ref") not in blood:
                        q.append((cr.get("ref"), f"{path} > child {view.gid(cr.get('ref'))}"))
        reach = dict(blood)
        fams = set()
        for h, path in blood.items():
            for fam in view.people[h].get("family_list") or []:
                fams.add(fam)
                for s in view.spouses(fam):
                    if s not in reach:
                        reach[s] = f"{path} > spouse {view.gid(s)}"
        return reach, fams

    def check(self, ctx):
        roots = [str(r) for r in ctx.section("scope").get("roots") or []]
        if not roots:
            raise NothingToCheck("no roots: give --root or [scope] roots")
        view = ctx.view
        handles = [view.resolve(r, "Person") for r in roots]
        missing = [r for r, h in zip(roots, handles) if h is None]
        if missing:
            raise NothingToCheck(f"root {', '.join(missing)} is not in the snapshot")
        reach, fams = self.reach(view, handles)
        out, seen = [], set()
        for op in ctx.ops:
            for cls, ref in refs(op):
                people, families, unknown = _people(view, cls, ref, families_as_spouses=False)
                if unknown is not None:
                    out.append(self.finding("scope-unknown", op, f"{cls} {unknown} is in neither the snapshot "
                                            "nor this list, so its place in the family cannot be checked",
                                            "export a fresh snapshot, or use the Gramps ID it has now"))
                for h in people:
                    if h in reach:
                        if h not in seen:
                            seen.add(h)
                            out.append(f"{view.label(h)}: {reach[h]}")
                    else:
                        out.append(self.finding("scope-out", op, f"{view.label(h)} is not a blood relative of "
                                                f"{', '.join(roots)} or the spouse of one"))
                for f in families:
                    if f not in fams:
                        out.append(self.finding("scope-out", op, f"family {view.gid(f)} has no parent who is a "
                                                f"blood relative of {', '.join(roots)}"))
        return _dedupe(out)

    def mutate(self, ctx):
        roots = ctx.section("scope").get("roots") or [_first_gid(ctx)]
        if not roots[0]:
            return None
        op = {"op": "add_person", "key": "lint-prove-scope", "ruling": "prove", "first_name": "Prove",
              "surname": "Stranger", "_line": len(ctx.ops) + 1}
        return Mutant(ctx.ops + [op], "scope-out", {"scope": {"roots": roots}})


def _dedupe(out):
    seen, res = set(), []
    for x in out:
        k = x if isinstance(x, str) else (x.code, x.key, x.message)
        if k not in seen:
            seen.add(k)
            res.append(x)
    return res


def _life_words(locales) -> set[str]:
    words = set()
    for code in locales:
        for ws in load_locale(code)["events"].values():
            words |= {fold(w) for w in ws}
    return words


def _mentions(text, names, words, window=8) -> bool:
    """Whether the text names someone in `names` (a Gramps ID, or a first name followed within four words by
    a surname word) with a life-event word at most `window` words away."""
    toks = fold(text).split()
    life = [i for i, t in enumerate(toks) if t in words]
    if not life:
        return False
    for gid, first, sur in names:
        hits = [i for i, t in enumerate(toks) if t == gid]
        if first and sur:
            hits += [i for i, t in enumerate(toks) if t == first and sur & set(toks[i + 1:i + 5])]
        if any(abs(i - j) <= window for i in hits for j in life):
            return True
    return False


class Protect(Rule):
    name = "protect"
    codes = {"protect-event": "adds or moves an event onto a protected person or their family",
             "protect-date": "changes the date of a protected person's event",
             "protect-place": "changes the place of a protected person's event",
             "protect-link": "adds a parent or child link to a protected person",
             "protect-attach": "attaches a note, citation, media, attribute or association to a protected person",
             "protect-update": "updates or merges a protected person, their family or their event",
             "protect-text": "new text names a protected person near a life-event word",
             "protect-unknown": "a protected Gramps ID is not in the snapshot"}

    def check(self, ctx):
        sec = ctx.section("protect")
        ids = [str(i) for i in sec.get("ids") or []]
        if not ids:
            raise NothingToCheck("no protected people: give --protect or [protect] ids")
        view = ctx.view
        out = []
        prot = set()
        for i in ids:
            h = view.resolve(i, "Person")
            if h is None:
                out.append(self.finding("protect-unknown", {"key": "", "_line": 0},
                                        f"protected {i} is not in the snapshot, so nothing protects them",
                                        "check the ID, or export a fresh snapshot"))
            else:
                prot.add(h)
        fams = {f for h in prot for f in view.people[h].get("family_list") or []}
        names = []
        for h in prot:
            n = view.people[h].get("primary_name") or {}
            sur = " ".join(s.get("surname") or "" for s in n.get("surname_list") or [])
            first = fold(n.get("first_name") or "").split()
            names.append((fold(view.gid(h)), first[0] if first else "", set(fold(sur).split())))
        words = _life_words(sec.get("locales") or LOCALES)

        def hit(cls, ref):
            people, families, _ = _people(view, cls, ref, families_as_spouses=False)
            who = [h for h in people if h in prot] + [f for f in families if f in fams]
            return [view.label(h) if h in view.people else f"family {view.gid(h)}" for h in who]

        for op in ctx.ops:
            kind = op["op"]
            if kind in ("add_event", "move_event"):
                owners = [("Person", op.get("person")), ("Family", op.get("family"))] if kind == "add_event" \
                    else attach_targets({"to": op.get("to")})
                for who in (w for cls, ref in owners if ref for w in hit(cls, ref)):
                    out.append(self.finding("protect-event", op, f"adds an event to protected {who}"))
            elif kind == "update_event":
                s = op.get("set") if isinstance(op.get("set"), dict) else {}
                for who in hit("Event", op.get("event")):
                    code = "protect-date" if "date" in s else "protect-place" if "place" in s else "protect-event"
                    out.append(self.finding(code, op, f"changes event {op.get('event')} of protected {who}"))
            elif kind in ("link_child", "unlink_child", "add_family", "update_child_ref"):
                cands = [("Person", op.get(f)) for f in ("child", "father", "mother")] + \
                        [("Person", c) for c in op.get("children") or []] + [("Family", op.get("family"))]
                for who in (w for cls, ref in cands if isinstance(ref, str) for w in hit(cls, ref)):
                    out.append(self.finding("protect-link", op, f"changes the family links of protected {who}"))
            elif kind in UPDATED or kind in MERGED:
                cands = [(UPDATED[kind], op.get(UPDATED[kind].lower()))] if kind in UPDATED else \
                        [(MERGED[kind], op.get(f)) for f in ("winner", "loser")]
                for who in (w for cls, ref in cands if isinstance(ref, str) for w in hit(cls, ref)):
                    out.append(self.finding("protect-update", op, f"{kind} changes protected {who}"))
            if kind not in ("add_event", "move_event", "update_event", "add_tag", "remove_tag", "detach"):
                targets = attach_targets(op) + ([("Person", op.get("person"))] if kind == "add_association" else [])
                for who in (w for cls, ref in targets for w in hit(cls, ref)):
                    out.append(self.finding("protect-attach", op, f"attaches to protected {who}"))
            for t in texts(op):
                if _mentions(t, names, words):
                    out.append(self.finding("protect-text", op, "new text names a protected person near a "
                                            "life-event word", "leave them out of the text"))
        return _dedupe(out)

    def mutate(self, ctx):
        ids = ctx.section("protect").get("ids") or [_first_gid(ctx)]
        if not ids[0]:
            return None
        op = {"op": "add_event", "key": "lint-prove-protect", "ruling": "prove", "type": "Residence",
              "person": ids[0], "date": "1950", "_line": len(ctx.ops) + 1}
        return Mutant(ctx.ops + [op], "protect-event", {"protect": {"ids": ids}})


class Living(Rule):
    name = "living"
    codes = {"living-date": "an exact date (with a day) on an event of someone who may be living",
             "living-place": "a place on an event of someone who may be living",
             "living-quote": "a quotation or transcript attached to someone who may be living",
             "living-marriage": "a marriage of someone who may be living",
             "living-id": "an identity number (DNI, NIE, NIR, codice fiscale, SSN) in new text"}

    def check(self, ctx):
        sec = ctx.section("living")
        view = ctx.view
        if not ctx.tree.handles["Person"]:
            raise NothingToCheck("the snapshot has no people")
        years, this_year = int(sec.get("years") or LIVING_YEARS), sec.get("this_year")
        cache = {}

        def living(h):
            if h not in cache:
                cache[h] = possibly_living(view.people[h], view.index, years=years, this_year=this_year)
            return cache[h]

        def alive(pairs):
            out = []
            for cls, ref in pairs:
                people, _, _ = _people(view, cls, ref)
                out += [f"{view.label(h)} ({living(h)[1]})" for h in people if living(h)[0]]
            return list(dict.fromkeys(out))

        out = []
        for op in ctx.ops:
            kind = op["op"]
            s = op.get("set") if isinstance(op.get("set"), dict) else {}
            if kind in ("add_event", "update_event"):
                owners = [("Person", op.get("person")), ("Family", op.get("family"))] if kind == "add_event" \
                    else [("Event", op.get("event"))]
                who = alive([(c, r) for c, r in owners if isinstance(r, str)])
                date = op.get("date") if kind == "add_event" else s.get("date")
                place = op.get("place") if kind == "add_event" else s.get("place")
                etype = op.get("type") if kind == "add_event" else s.get("type")
                for w in who:
                    if date and _exact(date):
                        out.append(self.finding("living-date", op, f"exact date {date} for {w}",
                                                "give the year only"))
                    if place:
                        out.append(self.finding("living-place", op, f"place {place} for {w}", "leave the place out"))
                    if etype == "Marriage":
                        out.append(self.finding("living-marriage", op, f"a marriage of {w}"))
            if kind in ("add_note", "add_transcription", "update_note"):
                text = op.get("text") if kind != "update_note" else s.get("text")
                if isinstance(text, str) and (kind == "add_transcription" or QUOTE.search(text)):
                    targets = attach_targets(op) if kind != "update_note" else self._note_owners(view, op.get("note"))
                    for w in alive(targets):
                        out.append(self.finding("living-quote", op, f"a quotation attached to {w}",
                                                "summarise it without the quotation"))
            for t in texts(op):
                for kind_, number in find_ids(t):
                    out.append(self.finding("living-id", op, f"a {kind_} number ({number[:3]}...) in new text",
                                            "leave it out; if it must stay, mark the note private"))
        return _dedupe(out)

    @staticmethod
    def _note_owners(view, ref):
        h = view.tree.gids["Note"].get(ref) if isinstance(ref, str) else None
        if not h:
            return []
        out = [("Person", p.get("gramps_id")) for p in view.people.values() if h in (p.get("note_list") or [])]
        out += [("Event", e.get("gramps_id")) for e in view.events.values() if h in (e.get("note_list") or [])]
        return out

    def mutate(self, ctx):
        person = {"op": "add_person", "key": "lint-prove-living", "ruling": "prove", "first_name": "Prove",
                  "surname": "Living", "_line": len(ctx.ops) + 1}
        birth = {"op": "add_event", "key": "lint-prove-living-birth", "ruling": "prove", "type": "Birth",
                 "person": "key:lint-prove-living", "date": "2001-05-04", "_line": len(ctx.ops) + 2}
        return Mutant(ctx.ops + [person, birth], "living-date")


OVERLAP_KINDS = ("Person", "Family", "Event", "Note", "Citation", "Media")
REPEATABLE = ("set_attribute", "add_tag", "remove_tag")


def _touched(op) -> set[str]:
    """The Gramps IDs an op touches, places, sources and repositories left out (lists share those freely)."""
    out = set()
    for cls, ref in refs(op):
        for r in (ref if cls == "ChildRef" else (ref,) if cls in OVERLAP_KINDS else ()):
            if isinstance(r, str) and not r.startswith("key:"):
                out.add(r)
    return out


def _same(op) -> str:
    return json.dumps({k: v for k, v in op.items() if k not in ("key", "ruling", "tag", "_line")}, sort_keys=True,
                      ensure_ascii=False)


class Overlap(Rule):
    name = "overlap"
    codes = {"overlap-open": "touches an object another open list touches",
             "overlap-applied": "repeats an update an applied list already made"}

    def check(self, ctx):
        sec = ctx.section("overlap")
        if ctx.injected is None and not sec.get("open") and not sec.get("applied"):
            raise NothingToCheck("no lists to compare with: give --open DIR or --applied DIR")
        out = []
        others = ctx.open_lists(sec.get("open")) if ctx.injected is not None or sec.get("open") else {}
        for name, ops in others.items():
            theirs = set().union(*(_touched(o) for o in ops)) if ops else set()
            for op in ctx.ops:
                both = _touched(op) & theirs
                if both:
                    out.append(self.finding("overlap-open", op, f"{', '.join(sorted(both))} also in open list {name}",
                                            "apply one list first, then rebase the other on a fresh snapshot"))
        if sec.get("applied") and ctx.injected is None:
            from .framework import read_lists
            done = {_same(o): name for name, ops in read_lists(sec["applied"], skip=ctx.path).items() for o in ops
                    if o["op"].startswith("update_") or o["op"] in REPEATABLE}
            for op in ctx.ops:
                if (op["op"].startswith("update_") or op["op"] in REPEATABLE) and _same(op) in done:
                    out.append(self.finding("overlap-applied", op, f"the same {op['op']} is in applied list "
                                            f"{done[_same(op)]}", "drop it: the tree already has it"))
        return out

    def mutate(self, ctx):
        gid = next((g for op in ctx.ops for g in sorted(_touched(op))), None) or _first_gid(ctx)
        if not gid:
            return None
        cls = next((c.lower() for c, h in ctx.tree.gids.items() if gid in h), "person")
        mine = {"op": "add_tag", "key": "lint-prove-overlap", "ruling": "prove", "target": {cls: gid},
                "name": "prove", "_line": len(ctx.ops) + 1}
        theirs = dict(mine, key="other", _line=1)
        return Mutant(ctx.ops + [mine], "overlap-open", open_lists={"prove-open.jsonl": [theirs]})


class Weak(Rule):
    name = "weak"
    codes = {"weak-untagged": "a weak claim (namesake, confidence 0-1, machine index) without the weak tag",
             "weak-inert-tag": "a `tag` on an op that makes no object, so it tags nothing"}

    def check(self, ctx):
        sec = ctx.section("weak")
        tag = sec.get("tag")
        if not tag:
            raise NothingToCheck("no weak tag: give --weak-tag or [weak] tag")
        machine = {str(s) for s in sec.get("machine_sources") or []}
        view = ctx.view
        tagged = {(c, r) for op in ctx.ops if op["op"] == "add_tag" and op.get("name") == tag
                  for c, r in attach_targets(op)}
        marked = set()  # child_refs (family, child) with a [probable] mark in the list
        for op in ctx.ops:
            if op["op"] == "link_child" and op.get("probable"):
                marked.add((op.get("family"), op.get("child")))
            if op["op"] == "add_note" and str(op.get("text") or "").startswith(PROBABLE):
                marked |= {r for c, r in attach_targets(op) if c == "ChildRef"}

        def has_tag(cls, ref):
            if (cls, ref) in tagged:
                return True
            h = view.resolve(ref, cls)
            obj = view.tree.handles.get(cls, {}).get(h) or {}
            return tag in {view.tree.tag_names.get(t) for t in obj.get("tag_list") or []}

        def probable(fam, child):
            if (fam, child) in marked:
                return True
            f = view.tree.handles["Family"].get(view.resolve(fam, "Family")) or {}
            c = view.resolve(child, "Person")
            notes = view.tree.handles["Note"]
            return any(text_of((notes.get(n) or {}).get("text")).startswith(PROBABLE)
                       for cr in f.get("child_ref_list") or [] if cr.get("ref") == c for n in cr.get("note_list") or [])

        out = []
        for op in ctx.ops:
            kind, own = op["op"], tag in tags_of(op)
            if tags_of(op) and not creates(op):
                out.append(self.finding("weak-inert-tag", op, f"{kind} makes no object, so its tag "
                                        f"{', '.join(tags_of(op))} lands nowhere", "use an add_tag op"))
            why, obj = None, None
            if kind == "add_person" and op.get("allow_namesake"):
                why, obj = "a namesake kept apart", ("Person", f"key:{op.get('key')}")
            elif kind == "add_citation" and (_low(op.get("confidence")) or str(op.get("source")) in machine):
                why, obj = "a weak citation", ("Citation", f"key:{op.get('key')}")
                refs_ = attach_targets(op)
                if refs_ and all(c == "ChildRef" and probable(*r) for c, r in refs_):
                    continue
            elif kind == "update_citation" and _low((op.get("set") or {}).get("confidence")
                                                    if isinstance(op.get("set"), dict) else None):
                why, obj = "a citation lowered to confidence 0-1", ("Citation", op.get("citation"))
                own = False
            if why and not own and not has_tag(*obj):
                out.append(self.finding("weak-untagged", op, f"{why} without the tag {tag}",
                                        f"add \"tag\": \"{tag}\"" if creates(op) else f"add an add_tag op ({tag})"))
        return out

    def mutate(self, ctx):
        tag = ctx.section("weak").get("tag") or "weak"
        op = {"op": "add_person", "key": "lint-prove-weak", "ruling": "prove", "first_name": "Prove",
              "surname": "Weak", "allow_namesake": True, "namesake_reason": "prove", "namesake_of": [],
              "_line": len(ctx.ops) + 1}
        return Mutant(ctx.ops + [op], "weak-untagged", {"weak": {"tag": tag}})


def _low(conf) -> bool:
    return isinstance(conf, int) and not isinstance(conf, bool) and conf <= 1


class Count(Rule):
    name = "count"
    codes = {"count-in-text": "new text counts what the tree holds, which goes stale as soon as another list lands"}

    def check(self, ctx):
        words = [fold(w) for w in ctx.section("count").get("words") or TREE_WORDS]
        out = []
        for op in ctx.ops:
            for t in texts(op):
                toks = fold(t).split()
                trees = [i for i, w in enumerate(toks) if any(w.startswith(x) for x in words)]
                nums = [i for i, w in enumerate(toks) if w.isdigit() and not (len(w) == 4 and 1000 <= int(w) < 2200)]
                if any(abs(i - j) <= 8 for i in trees for j in nums):
                    out.append(self.finding("count-in-text", op, f"counts what the tree holds: {t[:60]!r}",
                                            "write what the record says, or name the dated snapshot"))
        return out

    def mutate(self, ctx):
        gid = _first_gid(ctx)
        if not gid:
            return None
        word = (ctx.section("count").get("words") or TREE_WORDS)[0]
        op = {"op": "add_note", "key": "lint-prove-count", "ruling": "prove", "type": "Research",
              "text": f"The {word} has 12 people with this surname.", "attach": [{"person": gid}],
              "_line": len(ctx.ops) + 1}
        return Mutant(ctx.ops + [op], "count-in-text")


RULES = [Scope, Protect, Living, Overlap, Weak, Count]
