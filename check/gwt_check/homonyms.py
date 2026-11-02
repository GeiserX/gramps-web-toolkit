"""Read-only distant-homonym reporting and a duplicate-evidence guard."""
import datetime
from collections import defaultdict

from . import dates

def _homonym_birth_span(p: dict, events: dict) -> dates.Span | None:
    """All Primary Birth alternatives, widened rather than choosing a convenient year.

    An unreadable alternative makes the birth unknown. Baptism alone cannot establish
    a birth gap (adult baptisms exist), unlike the older duplicate hints above.
    """
    spans = []
    for r in p.get("event_ref_list", []):
        e = events.get(r.get("ref"))
        if not e or r.get("role") != "Primary" or dates.kind(e.get("type")) not in dates.BIRTH:
            continue
        span = dates.span(e.get("date"))
        if span is None:
            return None
        spans.append(span)
    if not spans:
        return None
    return dates.Span(min(s.lo for s in spans), max(s.hi for s in spans), all(s.firm for s in spans),
                      "; ".join(sorted({s.text for s in spans})))


def _homonym_birth_anniversary(s: dates.Span | None) -> int | None:
    """Ten civil years after the latest possible birth, or no usable bound.

    March 1 is the conservative anniversary of February 29 in a non-leap
    year. Open ends and endpoints outside the civil calendar stay unknown.
    Compute once per person, never once per same-name pair.
    """
    if s is None or not 1 <= s.hi <= datetime.date.max.toordinal():
        return None
    day = datetime.date.fromordinal(s.hi)
    if day.year + 10 > datetime.MAXYEAR:
        return None
    try:
        anniversary = day.replace(year=day.year + 10)
    except ValueError:  # February 29 has no anniversary day that year
        anniversary = datetime.date(day.year + 10, 3, 1)
    return anniversary.toordinal()


class _HomonymBirthGaps:
    """Accumulate scoped gaps while the caller retains the duplicate-hint guard."""

    def __init__(self, group: list[dict], events: dict, out):
        self.group = group
        self.out = out
        self.birth_spans = {p["handle"]: _homonym_birth_span(p, events) for p in group}
        self.anniversaries = {h: _homonym_birth_anniversary(s) for h, s in self.birth_spans.items()}
        self.gap_count = 0
        self.witness = None

    def record_gap(self, p: dict, q: dict) -> bool:
        """Record an incompatible pair; the caller has already checked its scope."""
        a, b = self.birth_spans[p["handle"]], self.birth_spans[q["handle"]]
        anniversary_a = self.anniversaries[p["handle"]]
        anniversary_b = self.anniversaries[q["handle"]]
        # Only a gap between the full intervals rules out a safe merge hint.
        if a and b and ((anniversary_a is not None and b.lo > anniversary_a)
                        or (anniversary_b is not None and a.lo > anniversary_b)):
            self.gap_count += 1
            if self.witness is None:
                self.witness = (p, q, a, b)
            return True
        return False

    def report(self, n: str) -> None:
        """Emit one bounded row, preserving the first pair as the witness."""
        if self.witness is not None:
            p, q, a, b = self.witness
            detail = (f"{p['gramps_id']} Birth {a.text}; {q['gramps_id']} Birth {b.text}; "
                      "possible births more than 10 civil years apart; not a safe merge candidate")
            if len(self.group) == 2:  # preserve existing pair keys for two-person groups
                self.out.add("homonym-birth-gap", f"{p['gramps_id']},{q['gramps_id']}", f"{n}: {detail}")
            else:
                self.out.add("homonym-birth-gap", f"name:{n}",
                             f"{n}: {self.gap_count} incompatible pairs (at least one person in scope) "
                             f"among {len(self.group)} same-name people; witness {detail}; "
                             "group summary, not a complete pair listing")


def install(T, out, in_scope, person_name, id_order):
    """Register one shared guard; retain no quadratic pair collection."""
    people = T["people"]
    groups = defaultdict(list)
    for person in people.values():
        name = person_name(person)
        if len(name.split()) >= 2:
            groups[name].append(person)
    guards = {}
    for name, group in groups.items():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda p: id_order(p["gramps_id"]))
        guard = _HomonymBirthGaps(group, T["events"], out)
        for person in group:
            guards[person["gramps_id"]] = (name, person, guard)
        _report_group(name, group, guard, in_scope)
    out.duplicate_guard = lambda key: _incompatible_key(key, guards)


def _report_group(name, group, guard, in_scope):
    for i, person in enumerate(group):
        for other in group[i + 1:]:
            if in_scope("people", person["handle"]) or in_scope("people", other["handle"]):
                guard.record_gap(person, other)
    guard.report(name)


def _incompatible_key(key, guards):
    pair = key.split(",")
    if len(pair) != 2 or pair[0] not in guards or pair[1] not in guards:
        return False
    name_a, person_a, guard = guards[pair[0]]
    name_b, person_b, _ = guards[pair[1]]
    if name_a != name_b:
        return False
    a, b = guard.birth_spans[person_a["handle"]], guard.birth_spans[person_b["handle"]]
    anniversary_a = guard.anniversaries[person_a["handle"]]
    anniversary_b = guard.anniversaries[person_b["handle"]]
    return bool(a and b and ((anniversary_a is not None and b.lo > anniversary_a)
                            or (anniversary_b is not None and a.lo > anniversary_b)))
