"""The dated checks: dates read as intervals, and contradictions only where the intervals cannot overlap."""

import json

import pytest

from gwt_check import dates
from gwt_check.checks import run_checks
from gwt_check.cli import main
from gwt_check.tree import Tree


def D(y=0, m=0, d=0, mod=0, q=0, text="", **kw):
    return {"calendar": 0, "dateval": [d, m, y, False], "modifier": mod, "newyear": 0, "quality": q,
            "text": text, **kw}


class Build:
    """An invented tree: people with dated events, families with children."""

    def __init__(self):
        self.d = {t: [] for t in ("people", "families", "events", "places", "citations", "sources", "media",
                                  "notes", "repositories")}
        self.n = 0

    def person(self, gid, gender=1, **events):
        refs = []
        for typ, date in events.items():
            self.n += 1
            self.d["events"].append({"handle": f"he{self.n}", "gramps_id": f"E{self.n}", "type": typ.capitalize(),
                                     "date": date})
            refs.append({"ref": f"he{self.n}", "role": "Primary"})
        types = [t.capitalize() for t in events]
        self.d["people"].append({"handle": "h" + gid, "gramps_id": gid, "gender": gender, "event_ref_list": refs,
                                 "birth_ref_index": types.index("Birth") if "Birth" in types else -1,
                                 "death_ref_index": types.index("Death") if "Death" in types else -1,
                                 "family_list": [], "parent_family_list": []})

    def family(self, fid, father=None, mother=None, kids=(), marriage=None, rel=None):
        refs = []
        if marriage:
            self.n += 1
            self.d["events"].append({"handle": f"he{self.n}", "gramps_id": f"E{self.n}", "type": "Marriage",
                                     "date": marriage})
            refs.append({"ref": f"he{self.n}", "role": "Family"})
        rel = rel if rel is not None else "Birth"
        self.d["families"].append({
            "handle": "h" + fid, "gramps_id": fid, "father_handle": "h" + father if father else None,
            "mother_handle": "h" + mother if mother else None, "event_ref_list": refs,
            "child_ref_list": [{"ref": "h" + k, "frel": rel, "mrel": rel} for k in kids]})
        people = {p["gramps_id"]: p for p in self.d["people"]}
        for x in (father, mother):
            if x:
                people[x]["family_list"].append("h" + fid)
        for k in kids:
            people[k]["parent_family_list"].append("h" + fid)

    def found(self, check=None):
        items, _ = run_checks(Tree(self.d))
        return {(f["check"], f["key"]) for f in items if check is None or f["check"] == check}

    def detail(self, check):
        items, _ = run_checks(Tree(self.d))
        return [f["detail"] for f in items if f["check"] == check]


# -- reading dates -----------------------------------------------------------------------------------

def test_dates_become_intervals():
    assert dates.span(D(1900, 2)).hi - dates.span(D(1900, 2)).lo == 27
    assert dates.span(D(1900, 12)).hi - dates.span(D(1900, 12)).lo == 30
    year = dates.span(D(1900))
    assert (year.hi - year.lo, year.firm) == (364, True)
    assert dates.span(D(0)) is None
    text, plain = dates.span(D(0, mod=6, text="1878")), dates.span(D(1878))
    assert (text.lo, text.hi, text.firm) == (plain.lo, plain.hi, False)
    assert dates.span(D(0, mod=6, text="free text")) is None


@pytest.mark.parametrize("date", [
    D(1900, mod=7),                                       # from 1900
    D(1900, mod=8),                                       # to 1900
    D(1700, 2, 10, newyear=3),                            # a year that starts on 25 March
    {**D(1700, 2, 10), "dateval": [10, 2, 1700, True]},   # dual-dated 1699/1700
    D(1700, calendar=1),                                  # Julian
], ids=["from", "to", "newyear", "dual-dated", "julian"])
def test_dates_it_cannot_read_safely_are_left_unread(date):
    assert dates.span(date) is None


def test_calculated_and_estimated_dates_are_soft_with_slack():
    plain, calc, est, about = (dates.span(D(1850, q=q, mod=m)) for q, m in ((0, 0), (2, 0), (1, 0), (0, 3)))
    assert calc.firm is False and est.firm is False and about.firm is False
    assert plain.lo - calc.lo == 366 and calc.hi - plain.hi == 366
    assert plain.lo - est.lo == 2 * 366 and plain.lo - about.lo == 2 * 366


# -- the rules -------------------------------------------------------------------------------------

def test_clean_family_gives_nothing():
    b = Build()
    b.person("I1", 1, birth=D(1860), death=D(1920))
    b.person("I2", 0, birth=D(1865), death=D(1930))
    b.person("I3", 0, birth=D(1890, 4, 1), baptism=D(1890, 4, 3))
    b.family("F1", "I1", "I2", ["I3"], marriage=D(1888))
    assert b.found() == set()


def test_child_baptised_after_mother_died_in_childbirth_is_not_impossible():
    """With no Birth event the child was born up to a year before the baptism, not on its day."""
    b = Build()
    b.person("I1", 0, birth=D(1730), death=D(1762, 2, 1))
    b.person("I2", 1, baptism=D(1762, 2, 2))
    b.family("F1", None, "I1", ["I2"])
    assert b.found("child-after-parent-death") == set()

    b = Build()  # control: a Birth event that day is impossible
    b.person("I1", 0, birth=D(1730), death=D(1762, 2, 1))
    b.person("I2", 1, birth=D(1762, 2, 2))
    b.family("F1", None, "I1", ["I2"])
    assert b.found("child-after-parent-death") == {("child-after-parent-death", "F1 I1 I2")}


def test_posthumous_father_window():
    b = Build()
    b.person("I1", 1, birth=D(1800), death=D(1840, 1, 1))
    b.person("I2", 1, birth=D(1840, 9, 1))     # 244 days later: possible
    b.person("I3", 1, birth=D(1841, 6, 1))     # 517 days later: not
    b.family("F1", "I1", None, ["I2", "I3"])
    assert b.found("child-after-parent-death") == {("child-after-parent-death", "F1 I1 I3")}


def test_calculated_parent_birth_gives_no_parent_age_finding():
    b = Build()
    b.person("I1", 1, birth=D(1850, q=2))
    b.person("I2", 1, birth=D(1863, 6, 1))
    b.family("F1", "I1", None, ["I2"])
    assert b.found("parent-age") == set()

    b = Build()  # control: a plain 1850 is a father of at most 13.4
    b.person("I1", 1, birth=D(1850))
    b.person("I2", 1, birth=D(1863, 6, 1))
    b.family("F1", "I1", None, ["I2"])
    assert b.found("parent-age") == {("parent-age", "F1 I1 I2")}
    assert b.detail("parent-age") == ["father at most 13.4 at the birth: father Birth E1 1850, "
                                      "child Birth E2 1863-06-01 (firm)"]


def test_about_slack_is_what_keeps_a_vague_date_quiet():
    b = Build()
    b.person("I1", 0, birth=D(1880, mod=3))
    b.person("I2", 1, birth=D(1893, 6, 1))
    b.family("F1", None, "I1", ["I2"])
    assert b.found("parent-age") == set()

    b = Build()
    b.person("I1", 0, birth=D(1880))
    b.person("I2", 1, birth=D(1893, 6, 1))
    b.family("F1", None, "I1", ["I2"])
    assert b.found("parent-age") == {("parent-age", "F1 I1 I2")}


@pytest.mark.parametrize("gender,born,found", [
    (0, D(1774, 6, 1), False),   # a bride of 12.9: lawful under canon law before 1917
    (0, D(1776, 1, 1), True),    # a bride of 11.4
    (1, D(1774, 6, 1), True),    # a groom of 12.9
], ids=["bride-12", "bride-11", "groom-12"])
def test_marriage_age_by_sex(gender, born, found):
    b = Build()
    b.person("I1", gender, birth=born)
    b.family("F1", "I1" if gender else None, None if gender else "I1", marriage=D(1787, 5, 1))
    assert bool(b.found("marriage-age")) is found


@pytest.mark.parametrize("rel,found", [("Birth", True), ({"string": "Birth"}, True), ("", True),
                                       ({"string": "Adopted"}, False), ("Stepchild", False)])
def test_child_relation_in_either_shape(rel, found):
    b = Build()
    b.person("I1", 0, birth=D(1800), death=D(1830))
    b.person("I2", 1, birth=D(1835))
    b.family("F1", None, "I1", ["I2"], rel=rel)
    assert bool(b.found("child-after-parent-death")) is found


def test_sibling_spacing_compares_births_only():
    b = Build()
    b.person("I1", 0, birth=D(1835))
    b.person("I2", 1, baptism=D(1860, 9, 1))   # the elder known only by a baptism, which can be late
    b.person("I3", 1, birth=D(1861, 3, 1))
    b.family("F1", None, "I1", ["I2", "I3"])
    assert b.found("sibling-spacing") == set()

    b = Build()  # control: two births 181 days apart
    b.person("I1", 0, birth=D(1835))
    b.person("I2", 1, birth=D(1860, 9, 1))
    b.person("I3", 1, birth=D(1861, 3, 1))
    b.person("I4", 1, birth=D(1861, 3, 2))     # a twin of I3
    b.family("F1", None, "I1", ["I2", "I3", "I4"])
    assert b.found("sibling-spacing") == {("sibling-spacing", "I1 I2,I3"), ("sibling-spacing", "I1 I2,I4")}


def test_event_order_names_the_event_types():
    b = Build()
    b.person("I1", 1, birth=D(1801), baptism=D(1800, 5, 3))
    assert b.found() == {("event-order", "I1 E1 E2")}
    assert b.detail("event-order") == ["Baptism E2 1800-05-03 is before Birth E1 1801 (firm)"]


def test_marriage_rules():
    b = Build()
    b.person("I1", 1, birth=D(1850), death=D(1880))
    b.person("I2", 0, birth=D(1890))
    b.family("F1", "I1", "I2", marriage=D(1885, mod=3))
    assert b.found() == {("marriage-after-death", "F1 E4 I1"), ("marriage-before-birth", "F1 E4 I2")}
    assert all(x.endswith("(soft)") for x in b.detail("marriage-after-death"))


def test_own_ancestor_without_recursion_on_a_long_line():
    b = Build()
    n = 5000
    for i in range(n):
        b.person(f"I{i}")
    for i in range(n - 1):
        b.family(f"F{i}", f"I{i + 1}", None, [f"I{i}"])
    assert b.found("own-ancestor") == set()
    b.family("Fx", "I0", None, [f"I{n - 1}"])
    got = b.found("own-ancestor")
    assert len(got) == 1 and next(iter(got))[1].startswith("I0,I1,I2,")


def test_lifespan_is_information_only(tmp_path):
    b = Build()
    b.person("I1", 1, birth=D(1700), death=D(1820))
    path = tmp_path / "t.json"
    path.write_text(json.dumps(b.d), encoding="utf-8")
    assert b.found() == {("lifespan", "I1")}
    assert main(["check", str(path)]) == 0


def test_baseline_fails_only_the_contradiction_a_block_wrote(tmp_path, capsys):
    before = Build()
    before.person("I1", 1, birth=D(1801), baptism=D(1800, 5, 3))   # a standing contradiction
    before.person("I2", 1, birth=D(1800), death=D(1840, 1, 1))
    old = tmp_path / "before.json"
    old.write_text(json.dumps(before.d), encoding="utf-8")
    after = Build()
    after.d = json.loads(old.read_text(encoding="utf-8"))
    after.n = 10
    after.person("I3", 1, birth=D(1850))                           # a namesake linked as the son
    after.family("F1", "I2", None, ["I3"])
    new = tmp_path / "after.json"
    new.write_text(json.dumps(after.d), encoding="utf-8")
    assert main(["check", str(old), "--baseline", str(old)]) == 0
    capsys.readouterr()
    assert main(["check", str(new), "--baseline", str(old)]) == 1
    out = capsys.readouterr().out
    assert "FAIL child-after-parent-death: 1 new" in out and "known event-order" in out


# -- guards each rule depends on ------------------------------------------------------------------

def test_slack_and_range_edges():
    plain = dates.span(D(1850))
    assert dates.span(D(1850, mod=1, q=1)).hi == plain.hi + 2 * 366     # est. before 1850
    assert dates.span(D(1850, mod=2, q=2)).lo == plain.lo - 366         # calc. after 1850
    rng = dict(D(1850, mod=4), dateval=[0, 0, 1850, False, 0, 0, 1849, False])
    assert dates.span(rng) is None                                      # a range that ends before it starts
    dual = dict(D(1850, mod=4), dateval=[0, 0, 1850, False, 10, 2, 1851, True])
    assert dates.span(dual) is None                                     # second date dual-dated


def test_about_in_free_text_gets_the_slack():
    for text, found in (("c. 1880", False), ("1880", True)):
        b = Build()
        b.person("I1", 0, birth=D(0, mod=6, text=text))
        b.person("I2", 1, birth=D(1893, 6, 1))
        b.family("F1", None, "I1", ["I2"])
        assert bool(b.found("parent-age")) is found, text


def test_only_the_persons_own_events_count():
    """A godparent's role in someone else's baptism is not the godparent's baptism."""
    b = Build()
    b.person("I1", 1, birth=D(1850))
    b.d["events"].append({"handle": "hx", "gramps_id": "E99", "type": "Baptism", "date": D(1840, 5, 1)})
    b.d["people"][0]["event_ref_list"].append({"ref": "hx", "role": "Godparent"})
    assert b.found() == set()


def test_the_birth_pointer_picks_the_birth():
    b = Build()
    b.person("I1", 1, death=D(1860))
    for gid, y in (("E98", 1870), ("E99", 1850)):
        b.d["events"].append({"handle": "h" + gid, "gramps_id": gid, "type": "Birth", "date": D(y)})
        b.d["people"][0]["event_ref_list"].append({"ref": "h" + gid, "role": "Primary"})
    b.d["people"][0]["birth_ref_index"] = 2
    assert b.found("event-order") == set()


def test_a_burial_bounds_the_death():
    b = Build()
    b.person("I1", 1, birth=D(1800), death=D(1850, 6, 1), burial=D(1850, 5, 1))
    assert b.found() == {("event-order", "I1 E2 E3")}

    b = Build()
    b.person("I1", 0, birth=D(1800), burial=D(1830))
    b.person("I2", 1, birth=D(1835))
    b.family("F1", None, "I1", ["I2"], marriage=D(1832))
    assert b.found() == {("child-after-parent-death", "F1 I1 I2"), ("marriage-after-death", "F1 E4 I1")}


def test_an_old_father():
    b = Build()
    b.person("I1", 1, birth=D(1700))
    b.person("I2", 1, birth=D(1790))
    b.family("F1", "I1", None, ["I2"])
    assert b.found("parent-age") == {("parent-age", "F1 I1 I2")}


def test_sibling_spacing_needs_exact_days_and_birth_children():
    b = Build()
    b.person("I1", 0, birth=D(1835))
    b.person("I2", 1, birth=D(1860))           # a year only: it may be any day of 1860
    b.person("I3", 1, birth=D(1860, 3, 1))
    b.family("F1", None, "I1", ["I2", "I3"])
    assert b.found("sibling-spacing") == set()

    b = Build()
    b.person("I1", 0, birth=D(1835))
    b.person("I2", 1, birth=D(1860, 1, 1))
    b.person("I3", 1, birth=D(1860, 4, 1))
    b.family("F1", None, "I1", ["I2", "I3"], rel="Adopted")
    assert b.found("sibling-spacing") == set()


def test_unknown_sex_is_held_to_the_lower_marriage_age():
    b = Build()
    b.person("I1", 2, birth=D(1774, 6, 1))
    b.family("F1", "I1", None, marriage=D(1787, 5, 1))
    assert b.found("marriage-age") == set()


def test_only_a_marriage_event_counts_as_the_marriage():
    b = Build()
    b.person("I1", 1, birth=D(1850))
    b.family("F1", "I1", None, marriage=D(1840))
    b.d["events"][-1]["type"] = "Engagement"
    assert b.found() == set()


def test_unread_dates_are_counted():
    b = Build()
    b.person("I1", 1, birth=D(1850, calendar=1), death=D(1900, mod=7))
    _, info = run_checks(Tree(b.d))
    assert info["dates not read"] == 2


def test_the_age_and_spacing_checks_never_fail_a_run(tmp_path):
    b = Build()
    b.person("I1", 0, birth=D(1880, 1, 1))
    b.person("I2", 1, birth=D(1890, 6, 1))     # mother of 10
    b.person("I3", 1, birth=D(1890, 9, 1))     # 92 days after his brother
    b.person("I4", 1, birth=D(1860))
    b.family("F1", "I4", "I1", ["I2", "I3"], marriage=D(1891, 6, 1))   # bride of 11
    path = tmp_path / "t.json"
    path.write_text(json.dumps(b.d), encoding="utf-8")
    assert {c for c, _ in b.found()} == {"parent-age", "marriage-age", "sibling-spacing"}
    assert main(["check", str(path)]) == 0
