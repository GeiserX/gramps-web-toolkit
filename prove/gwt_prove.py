"""gwt-prove: contradictions and relationship strength in a Gramps tree, offline, read only.

It reads a `gwt-check dump` or a gwt-apply snapshot and reports two things:

1. Contradictions between dated facts. Every date becomes an interval of days (an exact day, a month, a
   year, "about", "before", a range). A finding needs the intervals to be disjoint, never just far apart on
   a best guess, so a vague date can only produce a weak finding, not a loud one.
   - `impossible`: no way to read the dates that makes the facts true (death before birth, a child born
     before a parent, after the mother's death, or more than 300 days after the father's, a marriage after a
     spouse's death, two death or birth events that cannot both hold, a person who is their own ancestor).
   - `implausible`: biologically unlikely but not impossible (a mother under 14 or over 52 at a birth, a father
     under 14 or over 85, two children of one mother less than 8 months apart that are not twins, a lifespan
     over 105, a marriage before 14).
   Each finding says how firm the dates behind it are: `firm` when every date involved is a plain day, month
   or year, `soft` when one is about, estimated, before, after, a range or read from text.
2. Strength of each parent link. A link is graded from what is attached to it, never from names:
   direct, unverified, derived, hypothesis or unrecorded, read from the tree's own evidence notes and marks
   (see `link_grades`). Names never raise a grade: a link is only as strong as the act behind it.

It never writes. Dates it cannot read are counted, not guessed.
"""
from __future__ import annotations

import datetime
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "check"))

PROBABLE, CONFIRMED = "[probable]", "[confirmed]"
REL_EV = re.compile(r"^EVIDENCIA RELACION R\d+ \| hij[oa](?:/a)? (?P<child>\S+) \| (?P<role>[^|]+?) \| progenitor (?P<parent>\S+) \|"
                    r"(?:.*?Estado_hechos canon \(verbatim\): (?P<estado>[^|]*)\| Confianza canon \(verbatim\): (?P<conf>[^|]*))?", re.S)
REL_CANON = re.compile(r"^\[Canon R\d+\] (?P<role>[^|]+?) \| Estado: (?P<estado>[^|]*)\| Confianza: (?P<conf>[^|]*)\| Cita: (?P<cita>[^|]*)", re.S)
# phrases a note uses when the reading behind a link is not settled
WEAK = re.compile(r"(NO validada|NO cotejadas?|Solo DIRECCION|asociación, no filiación|DERIVADA|Deducción|pendiente cotejo|"
                  r"puede ser sufijo|lectura incierta|no es identidad confirmada)", re.I)
NEGATIVE = re.compile(r"^\s*(bounded negative|negativo acotado)", re.I)
NAME_ONLY = re.compile(r"\b(names?|nombres?|noms?|index|índice|indice|only|solo|sólo|coincid\w*)\b", re.I)
BIRTH = {"birth", "nacimiento", "naissance"}
BAPTISM = {"baptism", "christening", "bautismo", "bautizo", "baptême"}
DEATH = {"death", "defunción", "defuncion", "defunción y entierro", "décès"}
BURIAL = {"burial", "entierro", "cremation", "inhumation"}
MARRIAGE = {"marriage", "matrimonio", "mariage"}
ABOUT_YEARS = 2          # "about" and estimated dates: this many years either side
POSTHUMOUS_DAYS = 300    # a father's child can be born this long after his death
TWIN_DAYS = 1
MIN_SPACING = 240        # days between two children of one mother
MOTHER_AGE = (14, 52)
FATHER_AGE = (14, 85)
MAX_LIFESPAN = 105
MIN_MARRIAGE_AGE = 14
LIVING_AFTER = datetime.date.today().year - 100

_TEXT = re.compile(r"^\s*(?P<c>c\.?|ca\.?|about|circa)?\s*(?P<y>\d{4})(?:-(?P<m>\d{1,2})(?:-(?P<d>\d{1,2}))?)?\s*$", re.I)


class Span:
    """A date as an interval of proleptic Gregorian ordinals; `firm` when no guess went into it."""
    __slots__ = ("lo", "hi", "firm", "text", "precision")

    def __init__(self, lo, hi, firm, text, precision):
        self.lo, self.hi, self.firm, self.text, self.precision = lo, hi, firm, text, precision

    def __repr__(self):
        return f"Span({self.text})"


def _ord(y, m, d):
    try:
        return datetime.date(y, m, d).toordinal()
    except ValueError:
        return None


def _part(y, m, d):
    """(lo, hi, precision) of a possibly partial date."""
    if not y or not (1 <= y <= 9999):
        return None
    if m and d:
        o = _ord(y, m, d)
        return (o, o, "day") if o else None
    if m:
        lo = _ord(y, m, 1)
        hi = _ord(y + (m == 12), 1 if m == 12 else m + 1, 1)
        return (lo, hi - 1, "month") if lo and hi else None
    return _ord(y, 1, 1), _ord(y, 12, 31), "year"


def span(date) -> Span | None:
    """The interval of a Gramps date, or None when it cannot be read (no date, other calendar, free text)."""
    if not date:
        return None
    dv, mod, qual = date.get("dateval") or [0, 0, 0, False], date.get("modifier") or 0, date.get("quality") or 0
    text = date.get("text") or ""
    if date.get("calendar"):
        return None
    if mod == 6:  # text only: read the plain ISO shapes, nothing else
        m = _TEXT.match(text)
        if not m:
            return None
        got = _part(int(m["y"]), int(m["m"] or 0), int(m["d"] or 0))
        if not got:
            return None
        lo, hi, prec = got
        slack = ABOUT_YEARS * 366 if m["c"] else 0
        return Span(lo - slack, hi + slack, False, text, prec)
    d, mo, y = dv[0], dv[1], dv[2]
    first = _part(y, mo, d)
    if not first:
        return None
    lo, hi, prec = first
    label = f"{y:04d}" + (f"-{mo:02d}" if mo else "") + (f"-{d:02d}" if d else "")
    firm = qual in (0, 2) and mod == 0
    if mod in (4, 5) and len(dv) >= 8:
        second = _part(dv[6], dv[5], dv[4])
        if not second:
            return None
        return Span(lo, second[1], False, label + ".." + f"{dv[6]:04d}", prec)
    if mod == 1:
        return Span(-10 ** 9, hi, False, "before " + label, prec)
    if mod == 2:
        return Span(lo, 10 ** 9, False, "after " + label, prec)
    if mod == 3 or qual == 1:
        slack = ABOUT_YEARS * 366
        return Span(lo - slack, hi + slack, False, ("about " if mod == 3 else "est. ") + label, prec)
    return Span(lo, hi, firm, label, prec)


def years_between(a: Span, b: Span):
    """(min, max) years from a to b, from the interval ends."""
    return (b.lo - a.hi) / 365.25, (b.hi - a.lo) / 365.25


def _kind(t):
    return str(t if isinstance(t, str) else (t or {}).get("string", "")).strip().lower()


class Prover:
    def __init__(self, tree, include_living=False):
        self.T = tree.objs if hasattr(tree, "objs") else tree
        self.include_living = include_living
        self.findings: list[dict] = []
        self.coverage = defaultdict(int)
        P, F, E = self.T["people"], self.T["families"], self.T["events"]
        self.ev_of: dict[str, dict] = {}
        self.birth: dict[str, tuple] = {}   # person -> (event, span)
        self.death: dict[str, tuple] = {}
        self.bapt: dict[str, tuple] = {}
        self.burial: dict[str, tuple] = {}
        for h, p in P.items():
            for kind, store, names in (("birth", self.birth, BIRTH), ("bapt", self.bapt, BAPTISM),
                                       ("death", self.death, DEATH), ("burial", self.burial, BURIAL)):
                got = self._pick(p, names, "birth_ref_index" if kind == "birth" else "death_ref_index" if kind == "death" else None)
                if got:
                    store[h] = got
        self.children: dict[str, list] = defaultdict(list)   # person -> [(family, child)]
        for fh, f in F.items():
            for c in f.get("child_ref_list") or []:
                if c["ref"] in P:
                    for par in (f.get("father_handle"), f.get("mother_handle")):
                        if par in P:
                            self.children[par].append((fh, c["ref"]))

    # -- helpers ----------------------------------------------------------------

    def gid(self, t, h):
        return (self.T[t].get(h) or {}).get("gramps_id") or h

    def name(self, h):
        p = self.T["people"][h]
        n = p.get("primary_name") or {}
        sn = " ".join(s.get("surname", "") for s in n.get("surname_list") or [] if s.get("surname"))
        return f"{n.get('first_name', '')} {sn}".strip() or "(no name)"

    def _pick(self, p, names, idx_field):
        E = self.T["events"]
        refs = p.get("event_ref_list") or []
        cands = []
        for i, r in enumerate(refs):
            e = E.get(r.get("ref"))
            if e and r.get("role") == "Primary" and _kind(e.get("type")) in names:
                cands.append((i, e))
        if not cands:
            return None
        if idx_field is not None:
            i0 = p.get(idx_field, -1)
            cands.sort(key=lambda c: c[0] != i0)
        e = cands[0][1]
        s = span(e.get("date"))
        self.coverage["dates read" if s else "dates unread"] += 1
        return (e, s) if s else None

    def living(self, h):
        """May this person be alive: no death or burial, and nothing shows a birth before LIVING_AFTER."""
        if h in self.death or h in self.burial:
            return False
        b = self.birth.get(h) or self.bapt.get(h)
        if b and b[1] and b[1].hi < _ord(LIVING_AFTER, 1, 1):
            return False
        return True

    LEGAL = re.compile(r"legitim|reconoc|adopt|adopci|recognis|recogniz|légitim", re.I)

    def legal_hint(self, fam, child):
        """True when the family or the child's link says the parent is a legal one (recognition, legitimation)."""
        texts = [self._ntext(n) for n in fam.get("note_list") or []]
        for c in fam.get("child_ref_list") or []:
            if c["ref"] == child:
                texts += [self._ntext(n) for n in c.get("note_list") or []]
        return any(self.LEGAL.search(t) for t in texts)

    def add(self, rule, level, people, summary, evidence, firm, margin=None, note=None):
        if not self.include_living and any(self.living(p) for p in people):
            self.coverage["held back (may be living)"] += 1
            return
        self.findings.append({
            "rule": rule, "level": level, "firm": firm, "margin_days": margin,
            "people": [self.gid("people", p) for p in people], "summary": summary, "evidence": evidence,
            **({"note": note} if note else {}),
        })

    def ev_line(self, label, pair):
        e, s = pair
        n = len(e.get("citation_list") or [])
        return f"{label} {e.get('gramps_id')} {s.text}" + (f", {n} citation(s)" if n else ", uncited")

    # -- contradictions -----------------------------------------------------------

    def run(self):
        self.person_rules()
        self.parent_rules()
        self.family_rules()
        self.sibling_rules()
        self.cycle_rule()
        self.findings.sort(key=lambda f: ({"impossible": 0, "implausible": 1}.get(f["level"], 2), not f["firm"], f["rule"], f["people"]))
        return self.findings

    def person_rules(self):
        for h in self.T["people"]:
            b, d = self.birth.get(h), self.death.get(h)
            bp = self.bapt.get(h)
            nm = f"{self.name(h)} ({self.gid('people', h)})"
            if b and d and d[1].hi < b[1].lo:
                self.add("death-before-birth", "impossible", [h], f"{nm}: death is before birth",
                         [self.ev_line("birth", b), self.ev_line("death", d)], b[1].firm and d[1].firm, b[1].lo - d[1].hi)
            ref = b or None
            if bp and d and d[1].hi < bp[1].lo:
                self.add("death-before-baptism", "impossible", [h], f"{nm}: death is before baptism",
                         [self.ev_line("baptism", bp), self.ev_line("death", d)], bp[1].firm and d[1].firm, bp[1].lo - d[1].hi)
            if b and bp and bp[1].hi < b[1].lo:
                self.add("baptism-before-birth", "impossible", [h], f"{nm}: baptism is before birth",
                         [self.ev_line("birth", b), self.ev_line("baptism", bp)], b[1].firm and bp[1].firm, b[1].lo - bp[1].hi)
            bu = self.burial.get(h)
            if d and bu and bu[1].hi < d[1].lo:
                self.add("burial-before-death", "impossible", [h], f"{nm}: burial is before death",
                         [self.ev_line("death", d), self.ev_line("burial", bu)], d[1].firm and bu[1].firm, d[1].lo - bu[1].hi)
            start = b or bp
            end = d or bu
            if start and end:
                lo, hi = years_between(start[1], end[1])
                if lo > MAX_LIFESPAN:
                    self.add("lifespan", "implausible", [h], f"{nm}: lived at least {lo:.0f} years",
                             [self.ev_line("birth" if b else "baptism", start), self.ev_line("death" if d else "burial", end)],
                             start[1].firm and end[1].firm, int(lo * 365))
            # two vital events of one kind that cannot both hold
            for kind, names in (("birth", BIRTH), ("death", DEATH)):
                spans = []
                for r in self.T["people"][h].get("event_ref_list") or []:
                    e = self.T["events"].get(r.get("ref"))
                    if e and r.get("role") == "Primary" and _kind(e.get("type")) in names:
                        s = span(e.get("date"))
                        if s:
                            spans.append((e, s))
                for i in range(len(spans)):
                    for j in range(i + 1, len(spans)):
                        a, c = spans[i][1], spans[j][1]
                        gap = max(a.lo - c.hi, c.lo - a.hi)
                        if gap > 0:
                            self.add(f"two-{kind}s-disagree", "impossible", [h],
                                     f"{nm}: two {kind} events that cannot both be right",
                                     [self.ev_line(kind, spans[i]), self.ev_line(kind, spans[j])],
                                     a.firm and c.firm, gap)

    def parent_rules(self):
        F = self.T["families"]
        for fh, f in F.items():
            for role, handle, ages in (("mother", f.get("mother_handle"), MOTHER_AGE), ("father", f.get("father_handle"), FATHER_AGE)):
                if handle not in self.T["people"]:
                    continue
                pb = self.birth.get(handle) or self.bapt.get(handle)
                pd = self.death.get(handle)
                pnm = f"{self.name(handle)} ({self.gid('people', handle)})"
                for c in f.get("child_ref_list") or []:
                    ch = c["ref"]
                    if ch not in self.T["people"]:
                        continue
                    cb = self.birth.get(ch) or self.bapt.get(ch)
                    cnm = f"{self.name(ch)} ({self.gid('people', ch)})"
                    fam = self.gid("families", fh)
                    if not cb:
                        continue
                    adopted = (c.get("frel"), c.get("mrel")) != ("Birth", "Birth") and role and (c.get("mrel" if role == "mother" else "frel") not in ("Birth", None))
                    if pb:
                        if cb[1].hi < pb[1].lo:
                            self.add(f"child-before-{role}", "impossible", [handle, ch],
                                     f"{cnm} is born before their {role} {pnm} ({fam})",
                                     [self.ev_line("child", cb), self.ev_line(role, pb)], cb[1].firm and pb[1].firm, pb[1].lo - cb[1].hi)
                        elif not adopted:
                            lo, hi = years_between(pb[1], cb[1])
                            if hi < ages[0]:
                                self.add(f"{role}-too-young", "implausible", [handle, ch],
                                         f"{pnm} is under {ages[0]} at the birth of {cnm} ({fam})",
                                         [self.ev_line("child", cb), self.ev_line(role, pb)], cb[1].firm and pb[1].firm, int((ages[0] - hi) * 365),
                                         note="a legal parent (recognition or legitimation) recorded as a biological one: check the relation type" if self.legal_hint(f, ch) else None)
                            if lo > ages[1]:
                                self.add(f"{role}-too-old", "implausible", [handle, ch],
                                         f"{pnm} is over {ages[1]} at the birth of {cnm} ({fam})",
                                         [self.ev_line("child", cb), self.ev_line(role, pb)], cb[1].firm and pb[1].firm, int((lo - ages[1]) * 365),
                                         note="a legal parent (recognition or legitimation) recorded as a biological one: check the relation type" if self.legal_hint(f, ch) else None)
                    if pd:
                        limit = pd[1].hi + (POSTHUMOUS_DAYS if role == "father" else 0)
                        if cb[1].lo > limit and not adopted:
                            self.add(f"child-after-{role}-death", "impossible", [handle, ch],
                                     f"{cnm} is born after the death of their {role} {pnm} ({fam})",
                                     [self.ev_line("child", cb), self.ev_line(role + " death", pd)], cb[1].firm and pd[1].firm, cb[1].lo - limit)

    def family_rules(self):
        F, E = self.T["families"], self.T["events"]
        for fh, f in F.items():
            fam = self.gid("families", fh)
            mar = None
            for r in f.get("event_ref_list") or []:
                e = E.get(r.get("ref"))
                if e and _kind(e.get("type")) in MARRIAGE:
                    s = span(e.get("date"))
                    if s:
                        mar = (e, s)
                        break
            if not mar:
                continue
            for role, handle in (("father", f.get("father_handle")), ("mother", f.get("mother_handle"))):
                if handle not in self.T["people"]:
                    continue
                nm = f"{self.name(handle)} ({self.gid('people', handle)})"
                d, b = self.death.get(handle), self.birth.get(handle) or self.bapt.get(handle)
                if d and mar[1].lo > d[1].hi:
                    self.add("marriage-after-death", "impossible", [handle], f"{fam}: marriage is after the death of {role} {nm}",
                             [self.ev_line("marriage", mar), self.ev_line("death", d)], mar[1].firm and d[1].firm, mar[1].lo - d[1].hi)
                if b:
                    lo, hi = years_between(b[1], mar[1])
                    if mar[1].hi < b[1].lo:
                        self.add("marriage-before-birth", "impossible", [handle], f"{fam}: marriage is before the birth of {role} {nm}",
                                 [self.ev_line("marriage", mar), self.ev_line("birth", b)], mar[1].firm and b[1].firm, b[1].lo - mar[1].hi)
                    elif hi < MIN_MARRIAGE_AGE:
                        self.add("marriage-too-young", "implausible", [handle], f"{fam}: {role} {nm} is under {MIN_MARRIAGE_AGE} at the marriage",
                                 [self.ev_line("marriage", mar), self.ev_line("birth", b)], mar[1].firm and b[1].firm, int((MIN_MARRIAGE_AGE - hi) * 365))

    def sibling_rules(self):
        F = self.T["families"]
        for mh, rows in self.children.items():
            if self.T["people"][mh].get("gender") != 0:
                continue
            kids = []
            for fh, ch in rows:
                b = self.birth.get(ch) or self.bapt.get(ch)
                if b and b[1].lo == b[1].hi:
                    kids.append((b[1].lo, ch, b))
            kids.sort()
            seen = set()
            for i in range(len(kids)):
                for j in range(i + 1, len(kids)):
                    gap = kids[j][0] - kids[i][0]
                    if gap > MIN_SPACING:
                        break
                    if gap <= TWIN_DAYS or (kids[i][1], kids[j][1]) in seen:
                        continue
                    seen.add((kids[i][1], kids[j][1]))
                    a, c = kids[i][1], kids[j][1]
                    self.add("siblings-too-close", "implausible", [mh, a, c],
                             f"{self.name(mh)} ({self.gid('people', mh)}): children {self.name(a)} and {self.name(c)} are {gap} days apart",
                             [self.ev_line("child", kids[i][2]), self.ev_line("child", kids[j][2])],
                             kids[i][2][1].firm and kids[j][2][1].firm, MIN_SPACING - gap)

    def cycle_rule(self):
        P, F = self.T["people"], self.T["families"]
        parents: dict[str, set] = defaultdict(set)
        for f in F.values():
            for c in f.get("child_ref_list") or []:
                for par in (f.get("father_handle"), f.get("mother_handle")):
                    if par in P and c["ref"] in P:
                        parents[c["ref"]].add(par)
        state: dict[str, int] = {}
        reported = set()

        def visit(h, path):
            state[h] = 1
            for par in sorted(parents.get(h, ())):
                if state.get(par) == 1:
                    cyc = path[path.index(par):] + [par] if par in path else [h, par]
                    key = frozenset(cyc)
                    if key not in reported:
                        reported.add(key)
                        self.add("own-ancestor", "impossible", list(dict.fromkeys(cyc)),
                                 "a person is their own ancestor: " + " -> ".join(self.gid("people", x) for x in cyc), [], True, None)
                elif state.get(par) is None:
                    visit(par, path + [par])
            state[h] = 2
        sys.setrecursionlimit(max(1000, len(P) * 3))
        for h in sorted(P):
            if state.get(h) is None:
                visit(h, [h])

    # -- relationship strength --------------------------------------------------------

    def _ntext(self, h):
        return ((self.T["notes"].get(h) or {}).get("text") or {}).get("string") or ""

    def recorded(self, fam):
        """What the tree's own notes say about each parent link of a family.

        Two note shapes carry it: `EVIDENCIA RELACION R.. | hijo/a P.. | Padre biologico | progenitor P.. | ...`
        (one child and one parent) and `[Canon R..] Padre biologico | Estado: .. | Confianza: .. | Cita: ..`
        (one parent role, no child named). Returns {(child gid or None, role): record}."""
        out = {}
        for n in fam.get("note_list") or []:
            s = self._ntext(n)
            m = REL_EV.match(s)
            if m:
                role = "father" if m["role"].lower().startswith("padre") else "mother"
                rec = {"child": m["child"], "role": role, "parent": m["parent"], "estado": (m["estado"] or "").strip(),
                       "conf": (m["conf"] or "").strip(), "text": s, "cited": bool(re.search(r"citas canon C\d", s)) or "doc D" in s}
                out[(m["child"], role)] = rec
                continue
            m = REL_CANON.match(s)
            if m and m["role"].lower().startswith(("padre", "madre")):
                role = "father" if m["role"].lower().startswith("padre") else "mother"
                out.setdefault((None, role), {"child": None, "role": role, "parent": None, "estado": m["estado"].strip(),
                                              "conf": m["conf"].strip(), "text": s, "cited": "C0" in (m["cita"] or "")})
        return out

    def link_grades(self):
        """One row per child_ref. Kinds, strongest first:
        direct      the tree calls it documented and a citation backs it, and no note doubts the reading;
        unverified  documented, but a note on that link says direction, identity or the act was not checked;
        derived     the tree itself calls it probable or deduced (an uncle, a widow, a mention);
        hypothesis  the tree calls it a hypothesis;
        unrecorded  no evidence note: graded from the citations on the child and family alone."""
        T = self.T
        rows = []
        for fh, f in T["families"].items():
            rec = self.recorded(f)
            fam_cits = len(f.get("citation_list") or [])
            neg = sum(1 for n in f.get("note_list") or [] if NEGATIVE.match(self._ntext(n)))
            for c in f.get("child_ref_list") or []:
                ch = c["ref"]
                if ch not in T["people"]:
                    continue
                cg = self.gid("people", ch)
                texts = [self._ntext(n) for n in c.get("note_list") or []]
                marked = any(t.startswith(PROBABLE) for t in texts) and not any(t.startswith(CONFIRMED) for t in texts)
                per = {}
                for role in ("father", "mother"):
                    per[role] = rec.get((cg, role)) or (rec.get((None, role)) if sum(1 for x in f["child_ref_list"] if x["ref"] in T["people"]) == 1 or (None, role) in rec and not any(k[0] == cg for k in rec) and len(f["child_ref_list"]) == 1 else None)
                got = [r for r in per.values() if r]
                est = " ".join(r["estado"].lower() for r in got)
                weak = [w for r in got for w in WEAK.findall(r["text"])]
                cited = bool(c.get("citation_list")) or any(r["cited"] for r in got)
                if not got:
                    bev = None
                    for r in T["people"][ch].get("event_ref_list") or []:
                        e = T["events"].get(r.get("ref"))
                        if e and r.get("role") == "Primary" and _kind(e.get("type")) in BIRTH | BAPTISM:
                            bev = e
                            break
                    bc = len((bev or {}).get("citation_list") or [])
                    kind = "unrecorded"
                    why = "no evidence note; " + ("child's birth cited" if bc else "child's birth uncited") + ("; family cited" if fam_cits else "; family uncited")
                else:
                    if "hip" in est:
                        kind, why = "hypothesis", "tree says hypothesis"
                    elif "probable" in est or any(w.lower() in ("derivada", "deducción", "deduccion") for w in weak):
                        kind, why = "derived", "tree says probable or derived"
                    elif weak:
                        kind, why = "unverified", "a note on the link says: " + ", ".join(sorted({w.lower() for w in weak}))
                    elif cited:
                        kind, why = "direct", "documented and cited"
                    else:
                        kind, why = "unverified", "documented but no citation found"
                    if marked and kind == "direct":
                        kind, why = "derived", "marked [probable] on the link"
                rows.append({
                    "family": f.get("gramps_id"), "child": cg, "child_name": self.name(ch), "kind": kind, "why": why,
                    "marked_probable": marked, "conf": sorted({r["conf"] for r in got}), "living": self.living(ch),
                    "negative_notes": neg, "weak": sorted({w.lower() for w in weak}),
                    "parents": [self.gid("people", x) for x in (f.get("father_handle"), f.get("mother_handle")) if x in T["people"]],
                })
        return rows

    def overstated(self):
        """Links the structure shows as plain birth links while the tree's own evidence calls them weaker."""
        out = []
        for g in self.link_grades():
            if g["kind"] in ("derived", "hypothesis") and not g["marked_probable"]:
                out.append(g)
        return out


def ancestors(T, root_gid):
    P, F = T["people"], T["families"]
    by = {p.get("gramps_id"): h for h, p in P.items()}
    if root_gid not in by:
        return None
    seen, todo = {by[root_gid]}, [by[root_gid]]
    parents = defaultdict(set)
    for f in F.values():
        for c in f.get("child_ref_list") or []:
            for par in (f.get("father_handle"), f.get("mother_handle")):
                if par in P:
                    parents[c["ref"]].add(par)
    while todo:
        for par in parents.get(todo.pop(), ()):
            if par not in seen:
                seen.add(par)
                todo.append(par)
    return {P[h]["gramps_id"] for h in seen}


def main(argv=None):
    import argparse
    import json
    from gwt_check.tree import load, BadInput
    ap = argparse.ArgumentParser(prog="gwt-prove", description="Contradictions and link strength, offline and read only.")
    ap.add_argument("dump")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--links", action="store_true", help="also print the grade of every parent link")
    ap.add_argument("--include-living", action="store_true", help="do not hold back findings about people who may be alive")
    a = ap.parse_args(argv)
    try:
        tree = load(a.dump)
    except (BadInput, OSError) as e:
        print(f"gwt-prove: {e}", file=sys.stderr)
        return 2
    pr = Prover(tree, include_living=a.include_living)
    found = pr.run()
    grades = pr.link_grades()
    if a.json:
        print(json.dumps({"findings": found, "links": grades, "coverage": dict(pr.coverage)}, indent=1, ensure_ascii=False))
        return 1 if found else 0
    for f in found:
        print(f"[{f['level']}/{'firm' if f['firm'] else 'soft'}] {f['rule']}: {f['summary']}")
        for e in f["evidence"]:
            print("    " + e)
        if f.get("note"):
            print("    note: " + f["note"])
    from collections import Counter
    cnt = Counter(g["kind"] for g in grades)
    print(f"\n{len(found)} finding(s); parent links {dict(cnt)}; {dict(pr.coverage)}")
    over = pr.overstated()
    if over:
        print(f"\n{len(over)} link(s) the tree itself calls probable or hypothesis but shows as plain birth links:")
        for g in over:
            print(f"  {g['family']} {g['child']} {g['child_name']}: {g['kind']} ({g['why']}; confidence {', '.join(g['conf']) or '-'})")
    if a.links:
        for g in grades:
            print(f"{g['kind']:10} {g['family']} {g['child']} {g['child_name']}: {g['why']}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
