"""Kin-based duplicate hints and birth dates that cannot overlap. Invented people only."""

import copy

from gwt_check import checks, selftest
from gwt_check.tree import Tree

BEFORE, AFTER, ABOUT = 1, 2, 3


def _date(y, m=0, d=0, modifier=0, quality=0):
    return {**selftest._date(y, m, d, modifier), "quality": quality}


def _event(d, p, gid, typ, date, role="Primary"):
    ev = copy.deepcopy(selftest._get(d, "events", "E0001"))
    ev.update(handle="h" + gid, gramps_id=gid, type=typ, date=date, citation_list=[], tag_list=[])
    d["events"].append(ev)
    p["event_ref_list"].append(selftest._ref(ev["handle"], role=role))


def namesakes(a, b, *, first_b="Teo", baptisms=None):
    """Two «Teo Lumbre Vado», each the son of his own «Ciro Lumbre Vado»: a grandfather and a grandson.

    `a` and `b` are lists of Birth dates (empty: no birth); `baptisms` adds a Baptism to each."""
    d = selftest.clean_tree()
    for n, first, births in (("1", "Teo", a), ("2", first_b, b)):
        selftest._new_person(d, f"I090{n}", "Ciro", "Lumbre Vado", family=f"hF090{n}")
        selftest._new_family(d, f"F090{n}", f"hI090{n}", "", kids=[f"hI091{n}"])
        p = selftest._new_person(d, f"I091{n}", first, "Lumbre Vado", parent_family=f"hF090{n}")
        for i, date in enumerate(births):
            _event(d, p, f"E091{n}{i}", "Birth", date)
        if baptisms:
            _event(d, p, f"E092{n}", "Baptism", baptisms[int(n) - 1])
    return d


def hints(d):
    found, _ = checks.run_checks(Tree(d, source="t"))
    return {f["check"] for f in found if f["key"] == "I0911,I0912"}


def test_kin_hint_is_left_out_when_the_births_cannot_overlap():
    for a in ([_date(1795, modifier=BEFORE)],           # open: on main its year was ignored
              [_date(1750, modifier=BEFORE)],
              [_date(1796, quality=1)],                  # estimated: two years of slack, still short of 1800
              [_date(1806, modifier=AFTER)]):
        assert hints(namesakes(a, [_date(1800)])) == set(), a
        assert hints(namesakes([_date(1800)], a)) == set(), a


def test_kin_hint_stays_when_the_births_could_be_one():
    cases = [
        ([_date(1797, modifier=ABOUT)], [_date(1799, modifier=AFTER)]),  # about 1797: its slack reaches 1799
        ([_date(1800, 3, 2)], [_date(1800, 3, 2)]),                # the same day
        ([], [_date(1800)]),                                       # no birth on one side
        ([_date(1750, modifier=BEFORE), {**_date(0), "modifier": 6, "text": "algún día"}], [_date(1800)]),  # unreadable
        ([_date(1790), _date(1800)], [_date(1790)]),               # every alternative counts
        ([_date(1750), _date(1800)], [_date(1800)]),               # the latest alternative too
    ]
    for a, b in cases:
        assert "dup-person-relative" in hints(namesakes(a, b)), (a, b)
        assert "dup-person-relative" in hints(namesakes(b, a)), (b, a)


def test_only_the_persons_own_readable_births_count():
    d = namesakes([], [_date(1800)])
    p = next(x for x in d["people"] if x["gramps_id"] == "I0911")
    _event(d, p, "E0919", "Birth", _date(1750, modifier=BEFORE), role="Witness")  # someone else's birth
    p["event_ref_list"].append(selftest._ref("hE_missing", role="Primary"))         # a dangling reference
    assert "dup-person-relative" in hints(d)


def test_a_baptism_alone_never_sets_two_namesakes_apart():
    d = namesakes([_date(1800)], [], baptisms=[_date(1800), _date(1700, modifier=BEFORE)])
    assert "dup-person-relative" in hints(d)


def test_a_shared_baptism_year_keeps_the_birth_hint_even_when_the_births_cannot_overlap():
    d = namesakes([_date(1800)], [_date(1750, modifier=BEFORE)], baptisms=[_date(1800), _date(1800)])
    assert hints(d) == {"dup-person-birth"}


def test_fuzzy_kin_hint_is_left_out_when_the_births_cannot_overlap():
    def fuzzy(a, b):
        found, _ = checks.run_checks(Tree(namesakes(a, b, first_b="Teo Ramiro"), source="t"))
        return {(f["check"], f["key"]) for f in found} & {("dup-person-fuzzy", "I0911,I0912")}

    assert fuzzy([_date(1795, modifier=BEFORE)], [_date(1800)]) == set()
    assert fuzzy([_date(1800)], [_date(1795, modifier=BEFORE)]) == set()
    assert fuzzy([], [_date(1800)])  # the control: an unknown birth still gives the hint
