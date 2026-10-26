"""Citations whose own title (their page) says they are not about the event they are attached to. A report: it
never changes the tree.

An event is often cited by an act of another kind (a marriage act under a birth) or by a marginal mention written
in another act (a death noted in a birth act). The place and date such an act holds are that act's, not the
event's. Each row names the rule that fired, to be read in the act, not taken as a verdict.
"""

from __future__ import annotations

import re

from .dates import kind
from .fill import _fold
from .tree import Tree

# One table of record words. Add a language here, not a regex in a rule.
ACT_WORDS = {
    "birth": ("nacimiento", "bautismo", "bautizo", "baptismo",  # es
              "naissance", "bapteme",  # fr
              "birth", "baptism",  # en
              "nascita", "nascite", "battesimo", "battesimi"),  # it
    "death": ("defuncion", "entierro", "sepultura", "esquela",  # es
              "deces", "sepulture",  # fr
              "death", "burial",  # en
              "morte", "morti", "sepoltura", "sepolture"),  # it
    "marriage": ("matrimonio", "casamiento", "boda", "velacion", "desposorio",  # es
                 "mariage",  # fr
                 "marriage",  # en
                 "matrimoni", "processetti"),  # it
}
EVENT_ACT = {"birth": "birth", "baptism": "birth", "death": "death", "burial": "death", "marriage": "marriage"}
# a page that says the citation is a marginal mention in another act (a death noted in a birth act)
MARGINAL = ("mencion marginal", "mention marginale", "marginal mention", "marginal note")


def _acts(page: str) -> set[str]:
    """Which kinds of act the citation's own title (its page) names."""
    f = _fold(page)
    return {k for k, ws in ACT_WORDS.items() if any(re.search(rf"(?<!\w){w}(?:s|es)?(?!\w)", f) for w in ws)}


def citation_problem(event: dict, c: dict) -> tuple[str, str] | None:
    """(rule, why) when this citation's page says it is not about this kind of event, else None."""
    page = c.get("page") or ""
    want = EVENT_ACT.get(kind(event.get("type")))
    acts = _acts(page)
    if want and acts and want not in acts:
        return ("other-act", f"the event is a {want}, the citation's page names a {'/'.join(sorted(acts))} act: "
                             f"its place and date are that act's")
    if want in ("death", "marriage") and any(m in _fold(page) for m in MARGINAL):
        return ("marginal-mention", f"the {want} is cited by a marginal mention in another act: the act's place and "
                                    f"date are not the {want}'s")
    return None


def cites_report(tree: Tree) -> dict:
    T = tree.objs
    rows = []
    for h, e in T["events"].items():
        for ch in e.get("citation_list") or []:
            c = T["citations"].get(ch)
            prob = citation_problem(e, c) if c else None
            if prob:
                rows.append({"event": tree.gid("events", h), "kind": kind(e.get("type")),
                             "citation": tree.gid("citations", ch), "rule": prob[0], "why": prob[1]})
    rows.sort(key=lambda r: (r["rule"], r["event"]))
    return {"rows": rows}


def print_cites(rep: dict) -> None:
    rows = rep["rows"]
    print(f"{len(rows)} citation rows to read first (a report; nothing is changed):")
    for r in rows:
        print(f"  {r['event']} {r['kind']} {r['citation']}: {r['rule']} - {r['why']}")
