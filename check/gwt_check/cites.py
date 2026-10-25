"""Citations that do not say what the event they are attached to says. A report: it never changes the tree.

An event of one person is often cited by an act that is about someone else (the person is only the father, the
declarant or a witness), by an act of another kind (a marriage act under a birth), or carries a date the act
gives to somebody else. The place or date such an act holds is not the event's. Each row names the rule that
fired and the words that fired it, to be read in the act, not taken as a verdict.
"""

from __future__ import annotations

import re
import unicodedata

from .checks import PARTICLES
from .dates import kind
from .fill import _fold, _note_text
from .tree import Tree

ANDS = {"y", "et", "and"}  # joins two names; not the start of a surname written as two words
MIN_WORD = 3  # shorter words ("de", "la") are not names
# One table of record words. Add a language here, not a regex in a rule.
ACT_WORDS = {
    "birth": ("nacimiento", "naissance", "birth", "bautismo", "bapteme", "baptism", "baptismo"),
    "death": ("defuncion", "deces", "death", "entierro", "sepulture", "burial"),
    "marriage": ("matrimonio", "mariage", "marriage", "casamiento"),
}
EVENT_ACT = {"birth": "birth", "baptism": "birth", "death": "death", "burial": "death", "marriage": "marriage"}
# words that end the part of a sentence that names who the act is about
SUBJECT_END = ("nacio", "nacida", "nacido", "nace", "fallecio", "murio", "falleci", "esposa", "epouse", "hijo", "hija",
               "fils", "fille", "padres", "declarante", "comparecen", "comparecio", "casado", "casada", "bautizado",
               "bautizada", "born", "died", "son", "daughter", "wife", "husband")
# the words after which a name is a relation of the person the act is about
RELATION = (r"hij[oa]s? d", r"fils d", r"fille d", r"padres", r"parents", r"hij[oa] del", r"son of", r"daughter of")
# a sentence that opens with one of these is about the record, not about a person
RECORD_OPENERS = ("acta", "acte", "registro", "imagen", "image", "folio", "matrimonio", "defuncion", "nacimiento",
                  "mariage", "deces", "naissance", "tabla", "table", "pagina", "page")
# a page that says the citation is a marginal mention in another act (a death noted in a birth act)
MARGINAL = ("mencion marginal", "mention marginale", "marginal mention", "marginal note")
FULL_DATE = r"(\d{1,2})/(\d{1,2})/(\d{4})"
# an event description that says the event has no date of its own
NO_OWN_DATE = ("sin fecha propia", "no date of its own", "sans date propre", "no own date")


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^\w]+", _fold(s or "")) if len(w) >= MIN_WORD and w not in PARTICLES]


def _name_set(s: str) -> set[str]:
    """The name words of s, and each two neighbours written as one word: «du pont» also gives «dupont»."""
    toks = [t for t in re.split(r"[^\w]+", _fold(s or "")) if t]
    out = {t for t in toks if len(t) >= MIN_WORD and t not in PARTICLES}
    out |= {a + b for a, b in zip(toks, toks[1:]) if len(a) < MIN_WORD and a not in PARTICLES | ANDS and len(a + b) >= MIN_WORD}
    return out


def _citation_texts(tree: Tree, c: dict) -> tuple[str, list[str]]:
    N = tree.objs["notes"]
    notes = [_note_text(N[n]) for n in c.get("note_list") or [] if n in N]
    return c.get("page") or "", notes


def _acts(page: str) -> set[str]:
    """Which kinds of act the citation's own title (its page) names."""
    f = _fold(page)
    return {k for k, ws in ACT_WORDS.items() if any(re.search(rf"(?<!\w){w}", f) for w in ws)}


def _claim(notes: list[str]) -> str:
    for t in notes:
        m = re.search(r"valor afirmado:\s*(.+?)(?:\s+extracto:|\Z)", _fold(t), flags=re.S)
        if m:
            return " ".join(m[1].replace("(?)", "").split())  # «nazaire(?)» is a doubtful reading, not a bracket
    return ""


def _subject(claim: str) -> str:
    """The words before the first that ends the name: «tomas ortega» from «tomas ortega nacio en ...»."""
    out = []
    for w in re.findall(r"[\w']+|[,;:()]", claim):
        if w in ",;:(" or any(w.startswith(e) for e in SUBJECT_END):
            break
        out.append(w)
    return " ".join(out)


def _alias(claim: str, subject: str) -> str:
    """The words of a bracket that follows the subject at once: «luci (ana ortega, id) fallecio»."""
    i = claim.find(subject)
    m = re.match(r"\s*\(([^)]*)\)", claim[i + len(subject):]) if i >= 0 and subject else None
    return m[1] if m else ""


def _only_after_relation(claim: str, words: list[str]) -> bool:
    """The person's name words are all in what follows the first «hijo de», «fils des»... and not all before it."""
    m = re.search(r"(?<!\w)(?:" + "|".join(RELATION) + ")", claim)
    if not m:
        return False
    before, after = _name_set(claim[:m.start()]), _name_set(claim[m.start():])
    return bool(words) and not all(w in before for w in words) and all(w in after for w in words)


def _namesake(claim: str, words: list[str]) -> bool:
    """The person's name is both before and after the first «hijo de»: the subject may bear the same name."""
    m = re.search(r"(?<!\w)(?:" + "|".join(RELATION) + ")", claim)
    if not m or not words:
        return False
    before, after = _name_set(claim[:m.start()]), _name_set(claim[m.start():])
    return all(w in before for w in words) and all(w in after for w in words)


def _name_words(p: dict) -> list[str]:
    n = p.get("primary_name") or {}
    sur = next((s.get("surname") or "" for s in n.get("surname_list") or []), "")
    return _words(n.get("first_name") or "") + _words(sur)[:1]


def citation_problem(tree: Tree, event: dict, owner: dict | None, c: dict) -> tuple[str, str] | None:
    """(rule, why) when this citation does not speak of this event's person or kind of event, else None."""
    page, notes = _citation_texts(tree, c)
    want = EVENT_ACT.get(kind(event.get("type")))
    acts = _acts(page)
    if want and acts and want not in acts:
        return ("other-act", f"the event is a {want}, the citation's page names a {'/'.join(sorted(acts))} act: "
                             f"its place and date are that act's")
    if want in ("death", "marriage") and any(m in _fold(page) for m in MARGINAL):
        return ("marginal-mention", f"the {want} is cited by a marginal mention in another act: the act's place and "
                                    f"date are not the {want}'s")
    claim = _claim(notes)
    if want and owner is not None and claim:
        subject = _subject(claim)
        words = _name_words(owner)
        about_a_person = (len(subject.split()) >= 2 and not re.search(r"\d", subject)
                          and not subject.split()[0].startswith(RECORD_OPENERS))
        if (about_a_person and words and not all(w in _name_set(subject + " " + _alias(claim, subject)) for w in words)
                and all(w in _name_set(claim) for w in words)):
            return ("other-subject", f"the act is about «{subject}»; this person is named in it only as a relation")
        if not about_a_person and _only_after_relation(claim, words):
            return ("other-subject", "the act names this person only after «son/daughter of», as a relation of its subject")
        if not about_a_person and _namesake(claim, words):
            return ("namesake", "the act's subject and one of the subject's parents bear this person's name: "
                                "read which one this event is about")
    return None


def _owners(tree: Tree) -> dict[str, dict]:
    own: dict[str, dict] = {}
    for p in tree.objs["people"].values():
        for r in p.get("event_ref_list") or []:
            own.setdefault(r.get("ref"), p)
    return own


def _registration_date(claim: str) -> tuple[int, int, int] | None:
    """The day an act was written, when the claim gives it as «acta ... del DD/MM/YYYY» or «registro DD/MM/YYYY»."""
    m = (re.search(rf"(?:acta|acte)\s*(?:n[°o]?\s*\d+(?:/\d+)?\s*)?(?:del|du)\s*{FULL_DATE}", claim)
         or re.search(rf"registr\w*\s+(?:el\s+|le\s+)?{FULL_DATE}", claim))
    return (int(m[3]), int(m[2]), int(m[1])) if m else None


def cites_report(tree: Tree) -> dict:
    T = tree.objs
    own = _owners(tree)
    rows = []
    for h, e in T["events"].items():
        k = kind(e.get("type"))
        for ch in e.get("citation_list") or []:
            c = T["citations"].get(ch)
            if not c:
                continue
            prob = citation_problem(tree, e, own.get(h), c)
            if prob:
                rows.append({"event": tree.gid("events", h), "kind": k, "citation": tree.gid("citations", ch),
                             "rule": prob[0], "why": prob[1]})
        d = e.get("date") or {}
        dy, mo, yr = (d.get("dateval") or [0, 0, 0, False])[:3]
        if k in ("birth", "death") and yr and not d.get("modifier"):
            for ch in e.get("citation_list") or []:
                c = T["citations"].get(ch)
                claim = _claim(_citation_texts(tree, c)[1]) if c else ""
                reg = _registration_date(claim)
                days = {(int(m[3]), int(m[2]), int(m[1])) for m in re.finditer(FULL_DATE, claim)}
                if reg == (yr, mo, dy) and len(days - {reg}) >= 1:
                    rows.append({"event": tree.gid("events", h), "kind": k, "citation": tree.gid("citations", ch),
                                 "rule": "registration-date",
                                 "why": "the event's date is the day the act was written; the claim gives another date"})
        desc = _fold(e.get("description") or "")
        if d.get("year") and not d.get("modifier") and any(t in desc for t in NO_OWN_DATE):
            rows.append({"event": tree.gid("events", h), "kind": k, "citation": "", "rule": "date-not-its-own",
                         "why": "the description says the event has no date of its own, the date is a plain one"})
    rows.sort(key=lambda r: (r["rule"], r["event"]))
    return {"rows": rows}


def print_cites(rep: dict) -> None:
    rows = rep["rows"]
    print(f"{len(rows)} citation/date rows to read first (a report; nothing is changed):")
    for r in rows:
        print(f"  {r['event']} {r['kind']} {r['citation']}: {r['rule']} - {r['why']}")
