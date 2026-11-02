"""Homonym findings use invented people and dates only; runnable without pytest."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from gwt_check import checks, selftest
from gwt_check.cli import check, main
from gwt_check.tree import Tree
from gwt_check.synth import big_tree


def pair(a=None, b=None):
    data = selftest.clean_tree()
    selftest._new_person(data, "I9", "Álvaro", "de Bruma", birth=(1800,))
    selftest._new_person(data, "I10", "alvaro", "Vruma", birth=(1820,))
    for gid, date in (("I9", a), ("I10", b)):
        if date is not None:
            selftest._get(data, "events", "E" + gid)["date"] = date
    return data


def homonyms(data, scope=None):
    rows, _ = checks.run_checks(Tree(data), scope)
    return [r for r in rows if r["check"] == "homonym-birth-gap"]


class HomonymTests(unittest.TestCase):
    def test_strict_civil_anniversary_boundaries(self):
        cases = [
            ((2000, 1, 1), (2010, 1, 1), False),
            ((1996, 1, 1), (2006, 1, 1), False),
            ((2000, 1, 1), (2010, 1, 2), True),
            ((1996, 1, 1), (2006, 1, 2), True),
            # Use the later, March 1 anniversary for February 29 in a non-leap year.
            ((2000, 2, 29), (2010, 2, 28), False),
            ((2000, 2, 29), (2010, 3, 1), False),
            ((2000, 2, 29), (2010, 3, 2), True),
        ]
        for a, b, expected in cases:
            for first, second in ((a, b), (b, a)):
                with self.subTest(first=first, second=second):
                    self.assertEqual(bool(homonyms(pair(selftest._date(*first), selftest._date(*second)))), expected)

    def test_closest_interval_endpoints_and_open_bounds(self):
        cases = [
            (selftest._date(2000, 1), selftest._date(2010, 1, 31), False),
            (selftest._date(2000, 1), selftest._date(2010, 2, 1), True),
            (selftest._date(1720, modifier=1), selftest._date(1700, modifier=2), False),
            (selftest._date(2000, 1, 1, modifier=1), selftest._date(2010, 1, 1), False),
            (selftest._date(2000, 1, 1, modifier=1), selftest._date(2010, 1, 2), True),
            (selftest._date(), selftest._date(2010), False),
            (selftest._date(9998), selftest._date(9988), False),
            (selftest._date(1, modifier=3), selftest._date(30), True),
        ]
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(bool(homonyms(pair(a, b))), expected)
                self.assertEqual(bool(homonyms(pair(b, a))), expected)

    def test_group_summary_counts_witness_scope_and_every_guard(self):
        data = selftest.clean_tree()
        ids = [f"I{i}" for i in range(100, 124)]
        for i, gid in enumerate(ids):
            p = selftest._new_person(data, gid, "Inventada", "Bruma", birth=(1600 + 20 * i, 1, 1))
            selftest._event(data, p, "EB" + gid, "Baptism", (2100,))
            p["parent_family_list"] = ["hF0001"]
        rows, _ = checks.run_checks(Tree(data))
        summaries = [r for r in rows if r["check"] == "homonym-birth-gap"]
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["key"], "name:inventada vruma")
        self.assertIn("276 incompatible pairs", summary["detail"])
        self.assertIn("witness I100 Birth 1600-01-01; I101 Birth 1620-01-01", summary["detail"])
        self.assertIn("not a complete pair listing", summary["detail"])
        self.assertFalse([r for r in rows if r["check"] in {"dup-person-birth", "dup-person-relative"}
                          and set(r["key"].split(",")) <= set(ids)])
        for objects in data.values():
            objects.reverse()
        self.assertEqual(homonyms(data), summaries)
        scoped = homonyms(data, {("people", "hI123")})
        self.assertEqual(len(scoped), 1)
        self.assertIn("23 incompatible pairs", scoped[0]["detail"])
        self.assertIn("witness I100 Birth 1600-01-01; I123 Birth 2060-01-01", scoped[0]["detail"])
        self.assertEqual(scoped[0]["key"], summary["key"])
        self.assertEqual(homonyms(data, set()), [])

    def test_common_surname_information_is_bounded_by_name_groups(self):
        data = big_tree(6000, seed=7)
        for p in data["people"]:
            for surname in p["primary_name"]["surname_list"]:
                surname["surname"] = "Comun"
        rows = homonyms(data)
        names = {checks.person_name(p) for p in data["people"]}
        self.assertLessEqual(len(rows), len(names))
        self.assertTrue(rows)
        self.assertTrue(all("incompatible pairs" in r["detail"] for r in rows))

    def test_summary_does_not_suppress_compatible_or_unknown_group_members(self):
        data = pair()
        selftest._new_person(data, "I11", "Álvaro", "de Bruma", birth=(1800,))
        selftest._new_person(data, "I12", "Álvaro", "de Bruma", birth=(0,))
        rows, _ = checks.run_checks(Tree(data))
        summaries = [r for r in rows if r["check"] == "homonym-birth-gap"]
        self.assertEqual(len(summaries), 1)
        self.assertIn("2 incompatible pairs", summaries[0]["detail"])
        self.assertTrue(any(r["check"] == "dup-person-birth" and r["key"] == "I11,I9" for r in rows))
        self.assertEqual(homonyms(data, {("people", "hI12")}), [])

    def test_gap_is_informational_stable_and_read_only(self):
        data = pair()
        before = copy.deepcopy(data)
        rows = homonyms(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "I9,I10")
        self.assertIn("not a safe merge candidate", rows[0]["detail"])
        self.assertIn("1800", rows[0]["detail"])
        self.assertIn("1820", rows[0]["detail"])
        self.assertFalse(check(Tree(data))["new"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invented.json"
            path.write_text(json.dumps(data))
            self.assertEqual(main(["check", str(path)]), 0)
        self.assertEqual(data, before)
        for objects in data.values():
            objects.reverse()
        self.assertEqual(homonyms(data), rows)
        self.assertEqual(homonyms(data, set()), [])
        for gid in ("I9", "I10"):
            self.assertEqual(homonyms(data, {("people", "h" + gid)}), rows)

    def test_intervals_never_become_exact_years(self):
        cases = [
            (selftest._date(1810, 1, 1), False),
            (selftest._date(1811, 1, 1), True),
            (selftest._date(1810), False),
            (selftest._date(1811), True),
            (selftest._date(1812, modifier=3), False),
            (selftest._date(1820, modifier=3), True),
            ({**selftest._date(1812), "quality": 1}, False),
            ({**selftest._date(1820), "quality": 2}, True),
            (selftest._date(1820, modifier=1), False),
            (selftest._date(1820, modifier=2), True),
            (selftest._date(), False),
            ({"modifier": 6, "text": "unknown"}, False),
            ({**selftest._date(1820), "calendar": 1}, False),
            ({**selftest._date(1820), "newyear": 1}, False),
            ({**selftest._date(1820), "dateval": [0, 0, 1820, True]}, False),
            ({**selftest._date(1820), "modifier": 4,
              "dateval": [0, 0, 1805, False, 0, 0, 1820, False]}, False),
        ]
        for later, expected in cases:
            with self.subTest(later=later):
                self.assertEqual(bool(homonyms(pair(selftest._date(1800, 1, 1), later))), expected)
                self.assertEqual(bool(homonyms(pair(later, selftest._date(1800, 1, 1)))), expected)

    def test_only_own_births_and_all_alternatives_count(self):
        data = pair()
        event = selftest._get(data, "events", "EI10")
        event["type"] = "Baptism"
        self.assertEqual(homonyms(data), [])
        event["type"] = "Nacimiento"
        self.assertTrue(homonyms(data))
        p = selftest._get(data, "people", "I10")
        p["event_ref_list"][0]["role"] = "Witness"
        self.assertEqual(homonyms(data), [])
        p["event_ref_list"][0]["role"] = "Primary"
        for date in (selftest._date(1805), selftest._date()):
            with self.subTest(date=date):
                extra = copy.deepcopy(event)
                extra.update(handle="hEalternative", gramps_id="Ealternative", date=date)
                data["events"].append(extra)
                p["event_ref_list"].append(selftest._ref(extra["handle"], role="Primary"))
                self.assertEqual(homonyms(data), [])
                data["events"].pop()
                p["event_ref_list"].pop()

    def test_gap_guard_blocks_duplicate_hints_even_with_shared_baptism_and_relative(self):
        data = pair()
        # Conflicting baptism evidence must not override the distant Birth intervals.
        for gid in ("I9", "I10"):
            p = selftest._get(data, "people", gid)
            selftest._event(data, p, "EB" + gid, "Baptism", (1830,))
            p["parent_family_list"] = ["hF0001"]
        self.assertTrue(homonyms(data))
        rows, _ = checks.run_checks(Tree(data))
        self.assertFalse([r for r in rows if set(r["key"].split(",")) == {"I9", "I10"}
                          and r["check"].startswith("dup-person-")])

    def test_no_gap_is_not_merge_approval_and_names_must_match(self):
        data = pair(b=selftest._date(1800))
        self.assertFalse(homonyms(data))
        rows, _ = checks.run_checks(Tree(data))
        self.assertTrue(any(r["check"] == "dup-person-birth" and r["key"] == "I10,I9" for r in rows))
        data = pair()
        selftest._get(data, "people", "I10")["primary_name"]["first_name"] = "Otro"
        self.assertFalse(homonyms(data))


if __name__ == "__main__":
    unittest.main()
