"""Tests for gwt_core.rules on an invented tree. Run: cd core && python3 -m pytest -q"""
import os
import sys
import tomllib
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "apply"))

from gwt_core import rules  # noqa: E402
from gwt_core.rules import possibly_living  # noqa: E402

YEAR = 2026


class Tree:
    """An invented snapshot in the `gwt-check dump` shape, built a person at a time."""

    def __init__(self):
        self.snap = {"people": [], "families": [], "events": []}
        self.n = 0

    def _h(self, kind):
        self.n += 1
        return f"{kind}{self.n:04d}"

    def event(self, kind, year, **date):
        h = self._h("e")
        self.snap["events"].append({"handle": h, "type": kind, "date": {"dateval": [0, 0, year, False], **date}})
        return {"ref": h, "role": "Primary"}

    def dated(self, kind, **date):
        """A person whose only event is `kind` with this Gramps date."""
        p = self.person()
        h = self._h("e")
        self.snap["events"].append({"handle": h, "type": kind, "date": date})
        p["event_ref_list"].append({"ref": h, "role": "Primary"})
        return p

    def person(self, born=None, died=None, baptised=None):
        p = {"handle": self._h("p"), "event_ref_list": [], "family_list": [], "parent_family_list": [],
             "person_ref_list": []}
        if born:
            p["event_ref_list"].append(self.event("Birth", born))
        if baptised:
            p["event_ref_list"].append(self.event("Baptism", baptised))
        if died:
            p["event_ref_list"].append(self.event("Death", died))
        self.snap["people"].append(p)
        return p

    def family(self, parent, children=(), married=None):
        f = {"handle": self._h("f"), "father_handle": parent["handle"], "mother_handle": None,
             "child_ref_list": [{"ref": c["handle"]} for c in children], "event_ref_list": []}
        if married:
            f["event_ref_list"].append(self.event("Marriage", married))
        parent["family_list"].append(f["handle"])
        for c in children:
            c["parent_family_list"].append(f["handle"])
        self.snap["families"].append(f)
        return f

    def living(self, p, **kw):
        return possibly_living(p, self.snap, this_year=YEAR, **kw)


class Living(unittest.TestCase):
    def test_own_birth_decides(self):
        t = Tree()
        self.assertEqual(t.living(t.person(born=1880)), (False, "born-1880", 1880))
        self.assertEqual(t.living(t.person(born=1950)), (True, "born-1950", 1950))

    def test_threshold_is_110_years(self):
        t = Tree()
        self.assertEqual(t.living(t.person(born=YEAR - 110))[0], True)
        self.assertEqual(t.living(t.person(born=YEAR - 111))[0], False)
        self.assertEqual(t.living(t.person(born=YEAR - 105), years=100)[0], False)

    def test_baptism_counts_as_birth(self):
        t = Tree()
        self.assertEqual(t.living(t.person(baptised=1990)), (True, "baptised-1990", 1990))

    def test_a_recorded_death_is_never_living(self):
        t = Tree()
        self.assertEqual(t.living(t.person(born=1990, died=2020)), (False, "death-recorded", None))

    def test_a_godparents_event_is_not_their_birth(self):
        t = Tree()
        p = t.person()
        ref = t.event("Baptism", 2001)
        ref["role"] = "Godparent"
        p["event_ref_list"].append(ref)
        self.assertEqual(t.living(p, undated=False), (False, "no-dates", None))

    def test_child_born_1990_makes_an_undated_parent_living(self):
        t = Tree()
        p = t.person()
        t.family(p, [t.person(born=1990)])
        self.assertEqual(t.living(p), (True, "child-born-1990", 1976))

    def test_child_born_long_ago_makes_parent_not_living(self):
        t = Tree()
        p = t.person()
        t.family(p, [t.person(born=1890)])
        self.assertEqual(t.living(p), (False, "child-born-1890", 1876))

    def test_grandchild_bound_goes_two_generations_down(self):
        # grandchild born 1940: the grandparent was born by 1912, before 1916, so not living. With no gap
        # going down (the bound taken as 1940 itself) they would be wrongly counted as living.
        t = Tree()
        p, child = t.person(), t.person()
        t.family(p, [child])
        t.family(child, [t.person(born=1940)])
        self.assertEqual(t.living(p), (False, "grandchild-born-1940", 1912))

    def test_marriage_bound(self):
        t = Tree()
        p = t.person()
        t.family(p, married=1905)
        self.assertEqual(t.living(p), (False, "married-1905", 1891))
        q = t.person()
        t.family(q, married=1960)
        self.assertEqual(t.living(q), (True, "married-1960", 1946))

    def test_sibling_bound_uses_gap(self):
        t = Tree()
        p, sib = t.person(), t.person(born=1880)
        t.family(t.person(), [p, sib])
        self.assertEqual(t.living(p), (False, "sibling-born-1880", 1900))
        self.assertEqual(t.living(p, gap=40), (True, "sibling-born-1880", 1920))

    def test_tightest_bound_wins(self):
        t = Tree()
        p = t.person()
        t.family(p, [t.person(born=1990), t.person(born=1880)])
        self.assertEqual(t.living(p), (False, "child-born-1880", 1866))

    def test_dna_match_with_no_dates_is_living(self):
        t = Tree()
        tester, match = t.person(born=1980), t.person()
        tester["person_ref_list"].append({"ref": match["handle"], "rel": "DNA"})
        self.assertEqual(t.living(match, undated=False), (True, "dna-match", None))

    def test_dna_tester_with_no_dates_is_living(self):
        t = Tree()
        tester, match = t.person(), t.person()
        tester["person_ref_list"].append({"ref": match["handle"], "rel": "dna"})
        self.assertEqual(t.living(tester, undated=False), (True, "dna-match", None))

    def test_other_associations_are_not_dna(self):
        t = Tree()
        a, b = t.person(), t.person()
        a["person_ref_list"].append({"ref": b["handle"], "rel": "Godfather"})
        self.assertEqual(t.living(b, undated=False), (False, "no-dates", None))

    def test_dna_does_not_beat_a_recorded_date(self):
        t = Tree()
        tester, match = t.person(born=1980), t.person(born=1880)
        tester["person_ref_list"].append({"ref": match["handle"], "rel": "DNA"})
        self.assertEqual(t.living(match), (False, "born-1880", 1880))

    def test_undated_and_alone(self):
        t = Tree()
        p = t.person()
        self.assertEqual(t.living(p), (True, "no-dates", None))
        self.assertEqual(t.living(p, undated=False), (False, "no-dates", None))

    def test_an_uncertain_birth_is_read_by_its_latest_year(self):
        # cutoff 1916: a range or span that ends after it may be someone born in 1930
        t = Tree()
        rng = t.dated("Birth", modifier=rules.MOD_RANGE, dateval=[0, 0, 1900, False, 0, 0, 1930, False])
        self.assertEqual(t.living(rng), (True, "born-1930", 1930))
        span = t.dated("Birth", modifier=rules.MOD_SPAN, dateval=[0, 0, 1905, False, 0, 0, 1925, False])
        self.assertEqual(t.living(span), (True, "born-1925", 1925))
        old = t.dated("Birth", modifier=rules.MOD_RANGE, dateval=[0, 0, 1800, False, 0, 0, 1810, False])
        self.assertEqual(t.living(old), (False, "born-1810", 1810))

    def test_born_after_gives_no_upper_bound(self):
        t = Tree()
        late = t.dated("Birth", modifier=rules.MOD_AFTER, dateval=[0, 0, 1920, False])
        self.assertEqual(t.living(late, undated=False), (True, "born-after-1920", None))
        # «aft 1911» may be 1950: it never makes anyone not living by itself
        self.assertEqual(t.living(t.dated("Birth", modifier=rules.MOD_AFTER, dateval=[0, 0, 1911, False]))[0], True)

    def test_other_calendars_read_through_sortval(self):
        # Islamic 1400 and Persian 1359 are 1979/1980 CE: living, though their dateval years are below 1916
        t = Tree()
        islamic = t.dated("Birth", calendar=4, dateval=[1, 1, 1400, False], sortval=rules._sdn(1979, 11, 21))
        self.assertEqual(t.living(islamic), (True, "born-1979", 1979))
        persian = t.dated("Birth", calendar=5, dateval=[1, 1, 1359, False], sortval=rules._sdn(1980, 3, 21))
        self.assertEqual(t.living(persian), (True, "born-1980", 1980))
        hebrew = t.dated("Birth", calendar=2, dateval=[1, 1, 5600, False], sortval=rules._sdn(1839, 9, 9))
        self.assertEqual(t.living(hebrew), (False, "born-1839", 1839))

    def test_a_short_dateval_is_undated(self):
        t = Tree()
        self.assertEqual(t.living(t.dated("Birth", dateval=[0, 0])), (True, "no-dates", None))

    def test_any_dated_event_of_their_own_bounds_the_birth(self):
        # someone on an 1851 census was born by 1851
        t = Tree()
        self.assertEqual(t.living(t.dated("Census", dateval=[0, 0, 1851, False])), (False, "census-1851", 1851))
        self.assertEqual(t.living(t.dated("Military Service", dateval=[0, 0, 1990, False])),
                         (True, "military-service-1990", 1990))

    def test_any_dated_event_of_a_child_bounds_the_parent(self):
        # a child who died in 1750 was born by 1750, so the parent was born by 1736
        t = Tree()
        p = t.person()
        t.family(p, [t.person(died=1750)])
        self.assertEqual(t.living(p), (False, "child-death-1750", 1736))

    def test_a_parent_bounds_the_child(self):
        t = Tree()
        child = t.person()
        t.family(t.person(born=1700), [child])
        self.assertEqual(t.living(child), (False, "parent-born-1700", 1700 + rules.PARENT_MAX_AGE))
        young = t.person()
        t.family(t.person(born=1950), [young])
        self.assertEqual(t.living(young), (True, "parent-born-1950", 1950 + rules.PARENT_MAX_AGE))

    def test_a_spouse_bounds_the_person(self):
        t = Tree()
        p = t.person()
        f = t.family(p)
        spouse = t.person(born=1800)
        f["mother_handle"] = spouse["handle"]
        self.assertEqual(t.living(p), (False, "spouse-born-1800", 1820))
        self.assertEqual(t.living(p, gap=200), (True, "spouse-born-1800", 2000))

    def test_apply_snapshot_shape_and_index(self):
        t = Tree()
        p = t.person()
        t.family(p, [t.person(born=1990)])
        snap = {"format": "gwt-apply-snapshot/1",
                "objects": {"Person": t.snap["people"], "Family": t.snap["families"], "Event": t.snap["events"]}}
        self.assertEqual(possibly_living(p, snap, this_year=YEAR)[1], "child-born-1990")
        ix = rules.Index(t.snap)
        self.assertEqual(possibly_living(p, ix, this_year=YEAR)[1], "child-born-1990")


class Fold(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(rules.fold("  María-José  ÁLVAREZ, n.º 3 "), "maria jose alvarez n o 3")

    def test_spanish_pairs_and_particles(self):
        self.assertEqual(rules.fold("Bermúdez de Castro", "es"), rules.fold("Vermudez Castro", "es"))
        self.assertEqual(rules.fold("Ysabel", "es"), rules.fold("Isabel", "es"))
        self.assertNotEqual(rules.fold("Ysabel"), rules.fold("Isabel"))

    def test_particles_are_whole_words(self):
        self.assertEqual(rules.fold("Juan de Dios", "es"), "juan dios")
        self.assertEqual(rules.fold("Delgado", "es"), "delgado")

    def test_other_locales(self):
        self.assertEqual(rules.fold("Ludwig van Beethoven", "nl"), "ludwig beethoven")
        self.assertEqual(rules.fold("Ioannes", "la"), rules.fold("Joannes", "la"))
        self.assertEqual(rules.fold("Etxeberria", "eu"), rules.fold("Echeverría", "eu"))

    def test_unknown_locale(self):
        with self.assertRaises(ValueError):
            rules.fold("x", "xx")


class Dates(unittest.TestCase):
    GRAMMAR = ["1890", "1890-03", "1890-03-12", "abt 1890", "bef 1890-03", "aft 1890-03-12", "est 1890",
               "calc abt 1890", "bet 1880 and 1890", "from 1880-01 to 1890-12-31", "", None, "ABOUT 1900"]

    def test_same_as_gwt_apply(self):
        from gwt_apply.dates import parse_date
        for text in self.GRAMMAR:
            self.assertEqual(rules.read_date(text), parse_date(text), text)

    def test_refuses_what_it_cannot_read(self):
        from gwt_apply.dates import DateError
        for text in ["12 de marzo de 1890", "1890-13", "1899-02-29", "around 1890", "1890 or so"]:
            with self.assertRaises(rules.DateError, msg=text):
                rules.read_date(text)
            with self.assertRaises(DateError, msg=text):
                from gwt_apply.dates import parse_date
                parse_date(text)

    def test_written_dates_with_a_locale(self):
        cases = [("12 de marzo de 1890", "es", [12, 3, 1890]), ("abt 7bre 1750", "es", [0, 9, 1750]),
                 ("March 12th, 1890", "en", [12, 3, 1890]), ("le 1er août 1801", "fr", [1, 8, 1801]),
                 ("12. März 1890", "de", [12, 3, 1890]), ("3 xaneiro 1820", "gl", [3, 1, 1820]),
                 ("die 3 mensis Januarii anno Domini 1701", "la", [3, 1, 1701]), ("Mayo de 1900", "es", [0, 5, 1900])]
        for text, loc, want in cases:
            self.assertEqual(rules.read_date(text, loc)["dateval"][:3], want, text)
        self.assertEqual(rules.read_date("bef 3 marzo 1890", "es")["modifier"], rules.MOD_BEFORE)

    def test_written_dates_refused(self):
        for text in ["12 de marzo", "31 de febrero de 1890", "12 de marzo y abril de 1890", "marzo de 90",
                     "123 de marzo de 1890"]:
            with self.assertRaises(rules.DateError, msg=text):
                rules.read_date(text, "es")


class Locales(unittest.TestCase):
    DIR = Path(HERE) / "gwt_core" / "locales"

    def raw(self, code):
        with open(self.DIR / f"{code}.toml", "rb") as fh:
            return tomllib.load(fh)

    def test_every_listed_locale_has_a_file_and_no_more(self):
        self.assertEqual(sorted(p.stem for p in self.DIR.glob("*.toml")), sorted(rules.LOCALES))

    def test_same_keys_everywhere(self):
        shape = None
        for code in rules.LOCALES:
            tab = self.raw(code)
            self.assertEqual(tuple(tab), rules.SECTIONS, code)
            self.assertEqual(tab["code"], code)
            keys = {s: sorted(v) for s, v in tab.items() if isinstance(v, dict)}
            if shape is None:
                shape = keys
            self.assertEqual(keys, shape, f"{code} has different keys from {rules.LOCALES[0]}")
            self.assertEqual(sorted(tab["months"], key=int), [str(n) for n in range(1, 13)], code)

    def test_values_are_word_lists(self):
        for code in rules.LOCALES:
            tab = self.raw(code)
            for sec in ("months", "gender", "roles", "events"):
                for key, words in tab[sec].items():
                    self.assertTrue(words and all(isinstance(w, str) and w.strip() for w in words), (code, sec, key))
            for sec, key in (("fold", "pairs"), ("dedication", "pairs")):
                self.assertTrue(all(len(p) == 2 and all(p) for p in tab[sec][key]), (code, sec))

    def test_a_month_word_names_one_month(self):
        for code in rules.LOCALES:
            seen = {}
            for n, words in self.raw(code)["months"].items():
                for w in words:
                    f = rules.fold(w, code)
                    self.assertIn(seen.setdefault(f, n), (n,), f"{code}: {w!r} is month {seen[f]} and {n}")
            self.assertEqual(rules.month(self.raw(code)["months"]["5"][0].upper(), code), 5)

    def test_identity_numbers_live_in_the_code_not_in_a_language(self):
        for code in rules.LOCALES:
            self.assertNotIn("id_numbers", self.raw(code), code)


class Dedication(unittest.TestCase):
    def test_abbreviations_written_out(self):
        self.assertEqual(rules.dedication("Parroquia de N.ª S.ª de la Asunción", "es"),
                         rules.dedication("Parroquia de Nuestra Señora de la Asunción", "es"))
        self.assertEqual(rules.dedication("St. Mary's", "en"), "saint mary s")
        self.assertEqual(rules.dedication("Ste-Croix", "fr"), "sainte croix")

    def test_whole_words_only(self):
        self.assertEqual(rules.dedication("Stoke", "en"), "stoke")


class Ids(unittest.TestCase):
    def test_spanish(self):
        self.assertEqual(rules.find_ids("DNI 12345678Z"), [("dni", "12345678Z")])
        self.assertEqual(rules.find_ids("DNI 12345678A"), [])
        self.assertEqual(rules.find_ids("NIE X1234567L"), [("nie", "X1234567L")])
        self.assertEqual(rules.find_ids("NIE X1234567A"), [])
        self.assertEqual(rules.find_ids("foto_20230115B.jpg"), [])

    def test_french_nir(self):
        self.assertEqual(rules.find_ids("NIR 1 85 05 78 006 084 91"), [("nir", "1 85 05 78 006 084 91")])
        self.assertEqual(rules.find_ids("NIR 1 85 05 78 006 084 92"), [])
        self.assertEqual(rules.find_ids("2 69 05 2A 006 084 90"), [("nir", "2 69 05 2A 006 084 90")])

    def test_italian_codice_fiscale(self):
        self.assertEqual(rules.find_ids("CF RSSMRA85T10A562S"), [("cf", "RSSMRA85T10A562S")])
        self.assertEqual(rules.find_ids("CF RSSMRA85T10A562T"), [])

    def test_us_ssn(self):
        self.assertEqual(rules.find_ids("SSN 123-45-6789"), [("us_ssn", "123-45-6789")])
        for bad in ("000-45-6789", "666-45-6789", "900-45-6789", "123-00-6789", "123-45-0000"):
            self.assertEqual(rules.find_ids(bad), [], bad)

    def test_an_id_is_found_whatever_the_language_of_the_text(self):
        # a DNI in an English note, an SSN in a Spanish one
        self.assertEqual(rules.find_ids("Her Spanish ID card was 12345678Z."), [("dni", "12345678Z")])
        self.assertEqual(rules.find_ids("número de la seguridad social 123-45-6789"), [("us_ssn", "123-45-6789")])
        self.assertEqual(rules.find_ids("DNI 12345678Z, SSN 123-45-6789", kinds=("us_ssn",)),
                         [("us_ssn", "123-45-6789")])

    def test_only_ascii_digits(self):
        # Arabic-Indic and fullwidth digits are not an identity number, and never crash the check
        self.assertEqual(rules.find_ids("RSSMRA٨٥T10A562S"), [])
        self.assertEqual(rules.find_ids("DNI １２３４５６７８Z"), [])
        self.assertEqual(rules.find_ids("٠١٢-٤٥-٦٧٨٩"), [])


if __name__ == "__main__":
    unittest.main()
