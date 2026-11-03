"""Evidence rules: what a change list says about its records, checked against the records you saved.

    quote      every quotation in new text is in a cited record, a saved transcription, or a signed reading
    principal  every FamilySearch record ark a list cites is the record's principal, for the right event
    role       family roles the text gives a persona match the record; a record with two parents of one sex
    placement  sacraments sit on a church that matches the record's place as typed; civil events on a town

The records come from `--records DIR` (or `records` in lint.toml): any folder below it holding what gwt-read
writes (the `<key>.json` envelopes and `<key>.candidates.json`), raw GEDCOM X records, a gwt-fscache folder, or
plain `.txt` transcriptions. Nothing is fetched. An ark the list cites with no saved record makes the rule exit 2:
it could not look.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
import unicodedata
import zlib
from pathlib import Path

from gwt_core.rules import LOCALES, dedication, fold, load_locale

from .framework import InputError, Mutant, NothingToCheck, Rule, attach_targets, text_of, texts
from .rules_tree import QUOTE, QUOTE_WORDS

ARK = re.compile(r"(?:ark:/61903/)?\b([13]:1:[A-Z0-9]{2,}(?:-[A-Z0-9]+)+)")
FS = "https://www.familysearch.org/ark:/61903/"
ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|…|\.\.\.)\s*")
# the facts of the record's principal that back an event of each type
EVENT_FACTS = {"Birth": {"Birth"}, "Baptism": {"Baptism", "Christening"}, "Christening": {"Baptism", "Christening"},
               "Marriage": {"Marriage"}, "Death": {"Death"}, "Burial": {"Burial"}}
CHURCH_EVENTS = ["Baptism", "Christening", "Confirmation", "First Communion"]
CIVIL_EVENTS = ["Birth", "Death"]
CHURCH_TYPES = ["Church", "Parish", "Chapel"]
# words that say "a church" in a place name; dropped before two church names are compared
CHURCH_WORDS = {"iglesia", "parroquia", "parroquial", "ermita", "capilla", "catedral", "colegiata", "basilica",
                "convento", "monasterio", "santuario", "church", "parish", "chapel", "cathedral", "eglise", "paroisse",
                "chapelle", "chiesa", "parrocchia", "pieve", "igreja", "paroquia", "capela", "kirche", "pfarrei",
                "kapelle", "kerk", "parochie", "esglesia", "eliza", "parrokia", "igrexa", "ecclesia", "parochia"}
# words of a dedication that name nobody in particular; a church named only with these is generic
TITLE_WORDS = {"san", "santo", "santa", "santos", "santas", "santisimo", "santisima", "sant", "santi", "saint",
               "sainte", "st", "sankt", "sao", "nuestra", "senora", "nostra", "signora", "nossa", "senhora", "notre",
               "dame", "our", "lady", "virgen", "virgin", "vergine", "mare", "deu", "maria", "mary", "marie"}
PARTICLES = {"de", "del", "della", "di", "du", "des", "d", "of", "the", "la", "le", "l", "do", "da", "dos", "das",
             "von", "van", "zu", "a", "al", "y", "e", "i", "and"}
PARENTS = {"father", "mother", "parent"}


def _norm(s) -> str:
    """NFC, with any run of spaces or line breaks as one space: the only change a quotation may show."""
    return " ".join(unicodedata.normalize("NFC", s or "").split())


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            if k not in ("_line", "key"):
                yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def op_arks(op) -> list[str]:
    """Every FamilySearch ark anywhere in the op: page, description, reasons, note text."""
    found = []
    for s in _strings(op):
        for a in ARK.findall(s):
            if a not in found:
                found.append(a)
    return found


def _tail(uri) -> str:
    return str(uri or "").rstrip("/").rsplit("/", 1)[-1]


# --- saved records -------------------------------------------------------------------------------------------

class Records:
    """The records saved in a folder: personas by ark, the texts of each record, and transcriptions."""

    def __init__(self, folder):
        self.folder = str(folder)
        self.records: list[dict] = []
        self.by_ark: dict[str, tuple[dict, dict]] = {}
        self.thin: dict[str, tuple[dict, dict]] = {}   # candidate rows: used only for an ark no full record holds
        self.unread: list[str] = []                     # files that look like saved answers but could not be read
        self.bound: dict[str, list[str]] = {}   # ark -> transcriptions saved for it
        self.loose: list[str] = []              # transcriptions and page texts tied to no ark
        root = Path(folder)
        if not root.is_dir():
            raise NothingToCheck(f"no folder {folder}")
        for top, dirs, files in os.walk(root, followlinks=True):  # a folder of symlinks to other record folders
            dirs.sort()
            for name in sorted(files):
                p = Path(top) / name
                try:
                    self._read(p)
                except (AttributeError, TypeError, KeyError, IndexError):  # a JSON of another shape
                    self.unread.append(str(p))

    def __len__(self):
        return len(self.records) + len(self.loose) + len(self.bound)

    def _read(self, p):
        name = p.name
        if name.endswith(".txt"):
            text = p.read_text(encoding="utf-8", errors="replace")
            self._bind(ARK.findall(name) + ARK.findall(text.split("\n", 1)[0]), text)
            return
        if not (name.endswith(".json") or name.endswith(".json.gz")):
            return
        try:
            raw = p.read_bytes()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            doc = json.loads(raw)
        except (OSError, ValueError, EOFError, zlib.error):
            return
        if not isinstance(doc, dict):
            return
        if {"url", "status", "fetched"} <= set(doc):           # a gwt-fscache entry: the answer is the .body beside it
            body = p.with_suffix(".body")
            try:
                data = body.read_bytes()
            except OSError:
                return
            try:
                self._walk(json.loads(data))
            except ValueError:
                self._bind(ARK.findall(doc["url"]), data.decode("utf-8", "replace"))
            return
        if isinstance(doc.get("candidates"), list):
            for c in doc["candidates"]:
                self._candidate(c)
            return
        url = doc.get("final_url") or doc.get("url") or ""
        if isinstance(doc.get("text"), str) and doc["text"].strip():
            self._bind(ARK.findall(str(url)), doc["text"])
        self._walk(doc.get("json") if "json" in doc else doc)

    def _bind(self, arks, text):
        if arks:
            for a in arks:
                self.bound.setdefault(a, []).append(text)
        else:
            self.loose.append(text)

    def _walk(self, o):
        """Every GEDCOM X record (an object with a `persons` list) inside a saved answer."""
        if isinstance(o, dict):
            if isinstance(o.get("persons"), list):
                self._gedcomx(o)
                return
            for v in o.values():
                self._walk(v)
        elif isinstance(o, list):
            for v in o:
                self._walk(v)

    def _gedcomx(self, g):
        from gwt_read import place_orig  # the reader's own list of the place as the index typed it
        rec = {"personas": [], "rels": [], "place_orig": place_orig(g),
               "texts": [s for s in _strings(g) if s.strip() and not s.startswith(("http://", "https://", "#"))]}
        for p in g["persons"]:
            if not isinstance(p, dict):
                continue
            arks = [a for a in ARK.findall(json.dumps(p.get("identifiers") or {})) if a.startswith("1:1:")]
            display = p.get("display") if isinstance(p.get("display"), dict) else {}
            names = p.get("names") if isinstance(p.get("names"), list) else []
            forms = names[0].get("nameForms") if names and isinstance(names[0], dict) else None
            form = forms[0] if isinstance(forms, list) and forms and isinstance(forms[0], dict) else {}
            name = str(display.get("name") or form.get("fullText") or "")
            labels = " ".join(str(f.get("text") or f.get("value") or "") for f in p.get("fields") or []
                              if isinstance(f, dict) and re.search(r"ROLE|RELATIONSHIP", str(f.get("labelId", ""))))
            rec["personas"].append({
                "id": "#" + str(p.get("id", "")), "arks": arks, "principal": bool(p.get("principal")),
                "gender": _tail((p.get("gender") or {}).get("type")), "name": name,
                "role": " ".join(x for x in (str(display.get("role") or ""), labels) if x),
                "facts": {_tail(f.get("type")) for f in p.get("facts") or [] if isinstance(f, dict)}})
        by_id = {p["id"]: p for p in rec["personas"]}
        for r in g.get("relationships") or []:
            if isinstance(r, dict):
                a, b = (r.get("person1") or {}).get("resource"), (r.get("person2") or {}).get("resource")
                rec["rels"].append((_tail(r.get("type")), a, b))
                # FamilySearch keeps a marriage's fact on the Couple relationship, not on the bride or the groom
                facts = {_tail(f.get("type")) for f in r.get("facts") or [] if isinstance(f, dict)}
                for who in (a, b):
                    if who in by_id:
                        by_id[who]["facts"] |= facts
        self._add(rec)

    def _candidate(self, c):
        """A gwt-read candidate row: one persona, its role and principal flag, and the place as typed."""
        if not isinstance(c, dict) or c.get("source") not in (None, "familysearch"):
            return
        ark = str(c.get("id") or "")
        ark = ark if ark.startswith("1:1:") else "1:1:" + ark
        if not ARK.fullmatch(ark):
            return
        places = [str(e.get("place") or "") for e in (c.get("events") or {}).values() if isinstance(e, dict)]
        rec = {"personas": [{"id": "#" + ark, "arks": [ark], "principal": bool(c.get("principal")), "gender": "",
                             "name": str(c.get("name") or ""), "role": str(c.get("role") or ""), "facts": set()}],
               "rels": [], "place_orig": [x for x in c.get("place_orig") or [] if isinstance(x, str)],
               "texts": [s for s in _strings(c) if s.strip()] + places}
        self._add(rec, self.thin)

    def _add(self, rec, into=None):
        self.records.append(rec)
        into = self.by_ark if into is None else into
        for p in rec["personas"]:
            for a in p["arks"]:
                into.setdefault(a, (rec, p))

    def find(self, ark):
        """(record, persona) of a record ark: the full record when one is saved, else its candidate row (gwt-read
        writes both, and the row has no facts or relationships); NothingToCheck when neither was saved (NOREC)."""
        if ark in self.by_ark:
            return self.by_ark[ark]
        if ark in self.thin:
            return self.thin[ark]
        unread = f" ({len(self.unread)} files could not be read, the first {self.unread[0]})" if self.unread else ""
        raise NothingToCheck(f"{ark} has no saved record in {self.folder} (NOREC){unread}; save it first: "
                             f"gwt-read familysearch record {ark} --out {self.folder}")

    def texts_of(self, ark) -> list[str]:
        """Every field of the record an ark points into (record ark) and the transcriptions saved for it."""
        out = list(self.bound.get(ark, []))
        if ark.startswith("1:1:"):
            rec, _ = self.find(ark)
            out += rec["texts"]
            for p in rec["personas"]:
                for a in p["arks"]:
                    if a != ark:
                        out += self.bound.get(a, [])
        return out

    def every_text(self) -> list[str]:
        return [t for r in self.records for t in r["texts"]] + [t for v in self.bound.values() for t in v] + self.loose


def principals(rec):
    return [p for p in rec["personas"] if p["principal"]]


def relation(rec, persona) -> str:
    """What a persona is to the record's principal: father, mother, parent, spouse, child, godparent, witness,
    principal or other. Relationships first, then the role the index gives it."""
    if persona["principal"]:
        return "principal"
    heads = {p["id"] for p in principals(rec)}
    for kind, a, b in rec["rels"]:
        if kind == "ParentChild" and a == persona["id"] and b in heads:
            return {"Male": "father", "Female": "mother"}.get(persona["gender"], "parent")
        if kind == "ParentChild" and b == persona["id"] and a in heads:
            return "child"
        if kind == "Couple" and heads & {a, b} and persona["id"] in (a, b):
            return "spouse"
    claimed = role_words(persona["role"])
    for r in ("godparent", "witness", "father", "mother", "spouse", "child", "priest"):
        if r in claimed:
            return r
    return "other"


def _role_table():
    words = {}
    for code in LOCALES:
        for role, ws in load_locale(code)["roles"].items():
            for w in ws:
                words[fold(w)] = role
    return words


ROLE_WORDS = _role_table()


def role_words(text, near=None, window=3) -> set[str]:
    """The family roles a text names (by any locale's role words); with `near`, only those within `window` words
    of one of those words."""
    toks = fold(text).split()
    hits = []
    for i in range(len(toks)):
        for n in (2, 1):  # «teniente cura» before «cura»
            w = " ".join(toks[i:i + n])
            if w in ROLE_WORDS:
                hits.append((i, ROLE_WORDS[w]))
                break
    if near is None:
        return {r for _, r in hits}
    at = [i for i, t in enumerate(toks) if t in near]
    return {r for i, r in hits if any(abs(i - j) <= window for j in at)}


# --- what each op cites --------------------------------------------------------------------------------------

class Evidence:
    """The list read for evidence: what each op cites, directly or through what it attaches to, and the event
    types it speaks about."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.records = load(ctx)
        self.by_key = {f"key:{op['key']}": op for op in ctx.ops if op.get("key")}
        self.attaching: dict[str, list[dict]] = {}
        for op in ctx.ops:
            for _, ref in attach_targets(op):
                if isinstance(ref, str):
                    self.attaching.setdefault(ref, []).append(op)

    def _tree_page(self, cls, ref):
        if cls != "Citation" or not isinstance(ref, str):
            return []
        h = self.ctx.tree.gids.get("Citation", {}).get(ref)
        return ARK.findall(str((self.ctx.tree.handles["Citation"].get(h) or {}).get("page") or ""))

    def cited(self, op, depth=0) -> list[str]:
        """The arks behind an op: its own, those of what it attaches to, and those of citations on an event it adds."""
        out = list(op_arks(op))
        if depth < 3:
            for cls, ref in attach_targets(op):
                if isinstance(ref, str) and ref in self.by_key:
                    out += self.cited(self.by_key[ref], depth + 1)
                else:
                    out += self._tree_page(cls, ref)
        if op["op"] == "add_event" and op.get("key"):
            for c in self.attaching.get(f"key:{op['key']}", []):
                out += op_arks(c)
        return list(dict.fromkeys(out))

    def event_types(self, op, depth=0) -> set[str]:
        """The event types an op speaks about: its own, or those of the events and families it attaches to."""
        if op["op"] == "add_event":
            return {str(op.get("type") or "")}
        out = set()
        if depth < 3:
            for cls, ref in attach_targets(op):
                if isinstance(ref, str) and ref in self.by_key:
                    out |= self.event_types(self.by_key[ref], depth + 1)
                elif cls == "Event":
                    h = self.ctx.tree.gids.get("Event", {}).get(ref)
                    t = text_of((self.ctx.tree.handles["Event"].get(h) or {}).get("type"))
                    if t:
                        out.add(t)
                elif cls == "Family":
                    out.add("Family")
        return out


def load(ctx) -> Records:
    folder = ctx.records
    if not folder:
        raise NothingToCheck("no saved records: give --records DIR or records in lint.toml")
    cache = getattr(ctx, "_evidence_records", None)
    if cache is None or cache.folder != str(folder):
        cache = Records(folder)
        if not len(cache):
            raise NothingToCheck(f"no saved records in {folder}")
        ctx._evidence_records = cache
    return cache


def _first_person(ctx):
    return min(ctx.tree.gids["Person"]) if ctx.tree.gids["Person"] else None


def _line(ctx):
    return len(ctx.ops) + 1


# --- quote ---------------------------------------------------------------------------------------------------

def _found(quote, fields) -> bool:
    parts = [p for p in ELLIPSIS.split(_norm(quote)) if p]
    for f in fields:
        f, at = _norm(f), 0
        for part in parts:
            at = f.find(part, at)
            if at < 0:
                break
            at += len(part)
        else:
            if parts:
                return True
    return False


def quotations(text):
    for m in QUOTE.finditer(text):
        q = next(g for g in m.groups() if g)
        if len(q.split()) >= QUOTE_WORDS:
            yield q


def readings(ctx) -> set[str]:
    """The quotations someone read on an image and signed: rows of quote, image sha, reader."""
    path = ctx.section("quote").get("readings")
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh, delimiter="\t"))
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"readings {path}: {e}") from None
    return {_norm(r[0]) for r in rows if len(r) >= 3 and r[0].strip() and re.fullmatch(r"[0-9a-fA-F]{8,}", r[1].strip())
            and r[2].strip()}


class Quote(Rule):
    name = "quote"
    codes = {"quote-notfound": "a quotation is in none of the cited records' fields or saved transcriptions",
             "quote-image": "a quotation whose only source is an image has no signed reading row (STOP)"}

    def check(self, ctx):
        ev = Evidence(ctx)
        signed = readings(ctx)
        out = []
        for op in ctx.ops:
            spans = [q for t in texts(op) for q in quotations(t)]
            if not spans:
                continue
            arks = ev.cited(op)
            images = [a for a in arks if a.startswith("3:1:")] + \
                [r for c, r in attach_targets(op) if c == "Media"] + (["media"] if op["op"] == "add_media" else [])
            record_arks = [a for a in arks if a.startswith("1:1:")]
            fields = [t for a in arks for t in ev.records.texts_of(a)] if arks else ev.records.every_text()
            for q in spans:
                if _found(q, fields) or _norm(q) in signed:
                    continue
                short = q if len(q) <= 60 else q[:57] + "..."
                if images and not record_arks:
                    out.append(self.finding("quote-image", op, f"«{short}» has only an image behind it and no signed "
                                            "reading", "add a row to the readings file: quote, image sha, reader"))
                else:
                    where = ", ".join(record_arks) if record_arks else "any saved record"
                    out.append(self.finding("quote-notfound", op, f"«{short}» is not in {where}",
                                            "copy the words from the record, accents and all, or drop the quote marks"))
        return out

    def mutate(self, ctx):
        recs = load(ctx)
        for t in recs.every_text():
            words = _norm(t).split()
            if len(words) < QUOTE_WORDS + 1:
                continue
            span = " ".join(words[:QUOTE_WORDS + 2])
            changed = _flip_accent(span)
            if changed and not _found(changed, recs.every_text()):
                op = {"op": "add_note", "key": "lint-prove-quote", "ruling": "prove", "attach": [],
                      "text": f"The record says «{changed}».", "_line": _line(ctx)}
                return Mutant(ctx.ops + [op], "quote-notfound")
        return None


_ACCENT = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u"}


def _flip_accent(s):
    for i, c in enumerate(s):
        if c in _ACCENT:
            return s[:i] + _ACCENT[c] + s[i + 1:]
    return None


# --- principal -----------------------------------------------------------------------------------------------

class Principal(Rule):
    name = "principal"
    codes = {"principal-nonprincipal": "cites the persona of someone who is not the record's principal",
             "principal-event": "the record's principal has no fact of the event type the op speaks about"}

    def check(self, ctx):
        ev = Evidence(ctx)
        out = []
        for op in ctx.ops:
            types = ev.event_types(op)
            for ark in op_arks(op):
                if not ark.startswith("1:1:"):
                    continue
                rec, persona = ev.records.find(ark)
                rel = relation(rec, persona)
                heads = principals(rec)
                if rel != "principal" and not (rel == "spouse" and types & {"Marriage", "Family"}):
                    fix = ", ".join(FS + a for p in heads for a in p["arks"][:1]) or "open the record and cite its principal"
                    out.append(self.finding("principal-nonprincipal", op,
                                            f"{ark} is {persona['name'] or 'a persona'} ({rel}), not the record's "
                                            "principal", fix))
                    continue
                facts = set().union(*(p["facts"] for p in heads)) if heads else set()
                wanted = set().union(*(EVENT_FACTS.get(t, set()) for t in types)) if types else set()
                if facts and wanted and not facts & wanted:
                    out.append(self.finding("principal-event", op, f"{ark} is a record of {', '.join(sorted(facts))}, "
                                            f"not of {', '.join(sorted(types))}", "cite the record of that event"))
        return out

    def mutate(self, ctx):
        for rec in load(ctx).records:
            for p in rec["personas"]:
                if p["arks"] and relation(rec, p) not in ("principal", "spouse"):
                    op = {"op": "add_citation", "key": "lint-prove-principal", "ruling": "prove", "source": "S0",
                          "attach": [], "page": FS + p["arks"][0], "_line": _line(ctx)}
                    return Mutant(ctx.ops + [op], "principal-nonprincipal")
        return None


# --- role ----------------------------------------------------------------------------------------------------

class Role(Rule):
    name = "role"
    codes = {"role-mismatch": "the list gives a persona a family role the record does not give it",
             "role-samesex": "the record gives the principal two parents of the same sex: one is likely a godparent"}

    def check(self, ctx):
        ev = Evidence(ctx)
        out = []
        for op in ctx.ops:
            arks = [a for a in ev.cited(op) if a.startswith("1:1:")]
            text = " ".join(texts(op))
            seen = set()
            for ark in arks:
                rec, persona = ev.records.find(ark)
                if id(rec) not in seen:
                    seen.add(id(rec))
                    out += self._samesex(op, rec, ark)
                if persona["principal"] or ark not in op_arks(op):
                    continue
                rel = relation(rec, persona)
                names = {w for w in fold(persona["name"]).split() if len(w) > 2}
                claims = role_words(text, names) if names else set()
                if op["op"] in ("link_child", "add_family"):
                    claims.add("parent")
                if not claims:
                    continue
                if rel in claims or (rel in PARENTS and claims & PARENTS):
                    continue
                said = ", ".join(sorted(claims))
                out.append(self.finding("role-mismatch", op, f"the list calls {persona['name'] or ark} {said}; in "
                                        f"{ark} the record gives {rel}", "read the record's roles again"))
        return out

    def _samesex(self, op, rec, ark):
        heads = {p["id"] for p in principals(rec)}
        by_id = {p["id"]: p for p in rec["personas"]}
        sexes = [by_id[a]["gender"] for kind, a, b in rec["rels"]
                 if kind == "ParentChild" and b in heads and a in by_id and by_id[a]["gender"] in ("Male", "Female")]
        if len(sexes) >= 2 and len(set(sexes)) == 1:
            return [self.finding("role-samesex", op, f"{ark}: the record gives two {sexes[0].lower()} parents",
                                 "check the image: one of them is likely a godparent the index read as a parent")]
        return []

    def mutate(self, ctx):
        for rec in load(ctx).records:
            for p in rec["personas"]:
                rel = relation(rec, p)
                if p["arks"] and p["name"] and rel not in ("principal", "other"):
                    word = "godparent" if rel != "godparent" else "father"
                    op = {"op": "add_note", "key": "lint-prove-role", "ruling": "prove", "attach": [],
                          "text": f"{word}: {p['name']}, {FS}{p['arks'][0]}", "_line": _line(ctx)}
                    return Mutant(ctx.ops + [op], "role-mismatch")
        return None


# --- placement -----------------------------------------------------------------------------------------------

def _forms(text, locales) -> list[str]:
    return list(dict.fromkeys(dedication(text, loc) for loc in locales))


def _core(words) -> list[str]:
    """A church name without its church words and the particles at either end."""
    w = [x for x in words if x not in CHURCH_WORDS]
    while w and w[0] in PARTICLES:
        w = w[1:]
    while w and w[-1] in PARTICLES:
        w = w[:-1]
    return w


def church_matches(church, town, origs, locales) -> bool:
    """Whether a church name matches the place the record typed. The dedication must appear whole (titles and
    all, so «Santo Cristo» is not «Santísimo Cristo»); a generic one (only church and title words, such as
    «Iglesia de Santa María») also needs the town's name in the typed place."""
    for c in _forms(church, locales):
        core = _core(c.split())
        if not core:
            continue
        generic = not [w for w in core if w not in TITLE_WORDS and w not in PARTICLES]
        for o in origs:
            for of in _forms(o, locales):
                hay = f" {of} "
                if f" {' '.join(core)} " in hay and (not generic or (town and f" {fold(town)} " in hay)):
                    return True
    return False


class Placement(Rule):
    name = "placement"
    codes = {"placement-town": "a sacrament placed on a town, region or country, not on a church",
             "placement-civil": "a civil event placed on a church",
             "placement-parish": "a sacrament placed on a church the record does not name",
             "placement-override": "a place_orig override without a quote that is on the page"}

    def _settings(self, ctx):
        sec = ctx.section("placement")
        return ({fold(x) for x in sec.get("church") or CHURCH_EVENTS}, {fold(x) for x in sec.get("civil") or CIVIL_EVENTS},
                {fold(x) for x in sec.get("church_types") or CHURCH_TYPES}, sec.get("locales") or list(LOCALES),
                sec.get("overrides") if isinstance(sec.get("overrides"), dict) else {})

    def _place(self, ctx, ref):
        """(name, type, enclosing ref) of a place in the snapshot or added by the list."""
        if isinstance(ref, str) and ref.startswith("key:"):
            op = next((o for o in ctx.ops if o["op"] == "add_place" and f"key:{o.get('key')}" == ref), None)
            return (op.get("name") or "", op.get("type") or "", op.get("enclosed_by")) if op else None
        h = ctx.tree.gids.get("Place", {}).get(ref) if isinstance(ref, str) else None
        p = ctx.tree.handles["Place"].get(h)
        if not p:
            return None
        parent = next((r.get("ref") for r in p.get("placeref_list") or []), None)
        parent_gid = (ctx.tree.handles["Place"].get(parent) or {}).get("gramps_id")
        return ((p.get("name") or {}).get("value") or "", text_of(p.get("place_type")), parent_gid)

    def _events(self, ctx):
        """(op, event type, place ref, event ref) of every event the list places."""
        for op in ctx.ops:
            if op["op"] == "add_event" and op.get("place"):
                yield op, str(op.get("type") or ""), op["place"], f"key:{op.get('key')}"
            elif op["op"] == "update_event" and isinstance(op.get("set"), dict) and op["set"].get("place"):
                h = ctx.tree.gids.get("Event", {}).get(op.get("event"))
                t = op["set"].get("type") or text_of((ctx.tree.handles["Event"].get(h) or {}).get("type"))
                yield op, str(t or ""), op["set"]["place"], op.get("event")

    def check(self, ctx):
        ev = Evidence(ctx)
        church_ev, civil_ev, church_types, locales, overrides = self._settings(ctx)
        out = []
        for op, etype, ref, evref in self._events(ctx):
            kind = fold(etype)
            if kind not in church_ev and kind not in civil_ev:
                continue
            place = self._place(ctx, ref)
            if place is None:
                continue
            name, ptype, parent = place
            is_church = fold(ptype) in church_types
            arks = ev.cited(op)
            if op["op"] == "update_event":
                arks += [a for c in ev.attaching.get(evref, []) for a in ev.cited(c)]
                h = ctx.tree.gids.get("Event", {}).get(evref)
                for c in (ctx.tree.handles["Event"].get(h) or {}).get("citation_list") or []:
                    arks += ARK.findall(str((ctx.tree.handles["Citation"].get(c) or {}).get("page") or ""))
            arks = [a for a in dict.fromkeys(arks) if a.startswith("1:1:")]
            origs = [o for a in arks for o in ev.records.find(a)[0]["place_orig"]]
            if kind in civil_ev:
                if is_church:
                    out.append(self.finding("placement-civil", op, f"{etype} placed on {name} ({ptype}), a church",
                                            "place it on the municipality"))
                continue
            if not is_church:
                fix = f"place it on the church under {name}" + (f" that the record names: {origs[0]}" if origs else "")
                out.append(self.finding("placement-town", op, f"{etype} placed on {name} ({ptype or 'no type'}), "
                                        "not on a church", fix))
                continue
            ov = overrides.get(str(op.get("key")))
            if isinstance(ov, dict):
                quote, page = str(ov.get("quote") or ""), [t for a in arks for t in ev.records.texts_of(a)]
                page += [c.get("page") or "" for c in ev.attaching.get(evref, []) if c["op"] == "add_citation"]
                if quote.strip() and _found(quote, page) and isinstance(ov.get("place"), str):
                    origs = [ov["place"]]
                else:
                    out.append(self.finding("placement-override", op, "the override has no quote, or its quote is "
                                            "not on the cited page", "give the words of the page that name the church"))
            if not origs:
                continue
            town = (self._place(ctx, parent) or ("",))[0] if parent else ""
            if not church_matches(name, town, origs, locales):
                out.append(self.finding("placement-parish", op, f"{etype} placed on {name}; the record says "
                                        f"{' | '.join(origs)}", "place it on the church the record names, or give "
                                        "a place_orig override with the page's words"))
        return out

    def mutate(self, ctx):
        church_ev = self._settings(ctx)[0]
        etype = next((t for t in CHURCH_EVENTS if fold(t) in church_ev), None) or next(iter(church_ev), None)
        if not etype:
            return None
        etype = next((t for t in CHURCH_EVENTS if fold(t) == fold(etype)), etype)
        town = {"op": "add_place", "key": "lint-prove-town", "ruling": "prove", "name": "Prove Town", "type": "Town",
                "_line": _line(ctx)}
        event = {"op": "add_event", "key": "lint-prove-placement", "ruling": "prove", "type": etype,
                 "person": _first_person(ctx) or "key:lint-prove-town", "place": "key:lint-prove-town",
                 "_line": _line(ctx) + 1}
        return Mutant(ctx.ops + [town, event], "placement-town")


RULES = [Quote, Principal, Role, Placement]
