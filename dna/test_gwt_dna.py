"""gwt-import-dna on invented exports (fixtures/), and end to end through gwt-apply's fake Gramps Web."""
import contextlib
import copy
import gzip
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "core"), os.path.join(HERE, "..", "apply"),
                os.path.join(HERE, "..", "apply", "tests")]
import gwt_dna as D

try:
    from fake_api import FakeGrampsWeb
    from gwt_apply.client import Client
    from gwt_apply.dupes import preflight
    from gwt_apply.ops import Applier, validate
except ImportError:  # gwt-apply needs requests
    FakeGrampsWeb = None

FX = os.path.join(HERE, "fixtures")
fx = lambda name: os.path.join(FX, name)
PRIVATE = ["example.com/contact", "Elsewhere", "Nowhere", "@example.org", "kim@", "40-50", "1234567890",
           "Sample family tree", "R-M269"]


def tree(extra_people=(), notes=()):
    with open(fx("tree.json")) as fh:
        t = json.load(fh)
    t["objects"]["people"] += list(extra_people)
    t["objects"]["notes"] += list(notes)
    return t


def write_tree(d, t):
    p = os.path.join(d, "tree-%d.json" % len(os.listdir(d)))
    with open(p, "w") as fh:
        json.dump(t, fh)
    return p


def run(site, files, snapshot=fx("tree.json"), tested="I0001", id_map=None, **kw):
    matches, segments, cols = D.read_files([fx(f) for f in files], kw.pop("overrides", {}))
    amb = D.attach_segments(matches, segments)
    groups, rep = D.build(matches, D.load_tree(snapshot), site=site, tested=tested, kit=kw.pop("kit", tested),
                          ruling="R-1", tag="dna-match", id_map=id_map or {})
    rep["ambiguous"], rep["cols"] = amb, cols
    return [op for g in groups for op in g], groups, rep


def marked(gid, first, surname, site, mid, cm):
    """A person an earlier import left, with its mark note."""
    return ({"handle": "h" + gid, "gramps_id": gid, "primary_name": {"first_name": first, "surname_list": [
        {"surname": surname}]}, "note_list": ["n" + gid]},
            {"handle": "n" + gid, "text": {"_class": "StyledText", "string": f"{D.MARK} site={site} id={mid} cM={cm}\nx"}})


class Reading(unittest.TestCase):
    def test_myheritage_ops_one_group_per_match(self):
        ops, groups, rep = run("myheritage", ["myheritage-matches.csv", "myheritage-segments.csv"])
        self.assertEqual(rep["matches"], 4)
        self.assertEqual([g[0]["op"] for g in groups], ["add_person"] * 4)
        tom = next(g for g in groups if g[0].get("first_name") == "Tom")
        self.assertEqual([o["op"] for o in tom], ["add_person", "add_association", "add_note", "add_note"])
        self.assertEqual(tom[1], {"op": "add_association", "key": "dna-myheritage-D-11AA-I0001-assoc", "ruling": "R-1",
                                  "person": "I0001", "target": "key:dna-myheritage-D-11AA", "relation": "DNA"})
        self.assertTrue(tom[2]["text"].startswith("gwt-import-dna site=myheritage id=D-11AA cM=48\n"))
        self.assertEqual(tom[3]["attach"], [{"association": {"person": "I0001", "target": "key:dna-myheritage-D-11AA",
                                                             "relation": "DNA"}}])
        self.assertEqual(tom[0]["tag"], ["dna-match"])

    def test_contact_location_and_email_never_reach_the_change_list(self):
        for site, files in [("myheritage", ["myheritage-matches.csv", "myheritage-segments.csv"]),
                            ("ftdna", ["ftdna-matches.csv", "ftdna-segments.csv"]),
                            ("gedmatch", ["gedmatch-matches.csv", "gedmatch-segments.csv"])]:
            ops, _, rep = run(site, files)
            text = json.dumps(ops)
            for word in PRIVATE:
                self.assertNotIn(word, text, (site, word))
        ignored = run("myheritage", ["myheritage-matches.csv"])[2]["cols"][fx("myheritage-matches.csv")]["ignored"]
        self.assertTrue({"Contact", "Country", "Age"} <= set(ignored), ignored)

    def test_names_lose_email_domains_long_numbers_and_placeholders(self):
        self.assertEqual(D.clean_name("pat.lee@example.org"), "pat.lee")
        self.assertEqual(D.clean_name("Max 1234567890 Example"), "Max Example")
        self.assertEqual(D.clean_name("<Private> <Private>"), "")
        ops, _, _ = run("myheritage", ["myheritage-matches.csv"])
        self.assertIn("(no name, MyHeritage D-44DD)", [o.get("first_name") for o in ops])

    def test_numbers_as_sites_write_them(self):
        self.assertEqual(D.number("0.6% (48.0 cM)"), 48.0)
        self.assertEqual(D.number("227,6"), 227.6)
        self.assertEqual(D.number("1,234.5"), 1234.5)
        self.assertEqual(D.number("1.234,5"), 1234.5)
        self.assertEqual(D.number("30,000,000"), 30000000)
        self.assertIsNone(D.number(""))

    def test_gedmatch_segment_file_alone_makes_the_matches(self):
        ops, groups, rep = run("gedmatch", ["gedmatch-segments.csv"])
        self.assertEqual(rep["matches"], 2)
        kim = next(g for g in groups if g[0].get("first_name") == "Kim")
        self.assertIn("cM=28\n", kim[2]["text"])   # 18.2 + 9.8
        self.assertEqual(D.parse_like_gramps_web(kim[3]["text"])[0]["chromosome"], "4")

    def test_ftdna_two_matches_with_one_name_are_two_people_and_their_segments_wait(self):
        ops, _, rep = run("ftdna", ["ftdna-matches.csv", "ftdna-segments.csv"])
        lees = [o for o in ops if o["op"] == "add_person" and o["first_name"] == "Lee"]
        self.assertEqual(len(lees), 2)
        self.assertEqual(lees[1]["namesake_of"], ["key:" + lees[0]["key"]])
        self.assertEqual(rep["ambiguous"], ["Lee Ward"])
        self.assertFalse([o for o in ops if "lee-ward" in o["key"] and o["key"].endswith("-segments")])

    def test_column_override_and_unknown_column(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.csv")
            with open(p, "w") as fh:
                fh.write("Who,Amount\nAl Sample,33\n")
            m, _, _ = D.read_files([p], {"name": "Who", "cm": "Amount"})
            self.assertEqual(next(iter(m.values()))["cm"], 33)
            with self.assertRaises(D.BadInput):
                D.read_files([p], {"name": "Nobody"})
            with self.assertRaises(D.BadInput):
                D.read_files([p], {})

    def files(self, d, **texts):
        out = []
        for name, text in texts.items():
            out.append(os.path.join(d, name + ".csv"))
            with open(out[-1], "w") as fh:
                fh.write(text)
        return out

    def test_a_segment_row_joins_its_own_id_never_a_namesakes(self):
        with tempfile.TemporaryDirectory() as d:
            paths = self.files(d, m="DNA Match ID,Name,Shared DNA\nD-11AA,Tom Sample,0.6% (48.0 cM)\n",
                               s="DNA Match ID,Match name,Chromosome,Start Location,End Location,Centimorgans,SNPs\n"
                                 "D-11AA,Tom Sample,7,1000000,9000000,12.5,1500\n"
                                 "D-99ZZ,Tom Sample,3,5000000,90000000,95.0,9000\n"
                                 "AB 12,Al Sample,5,1000000,2000000,8.0,800\n")
            m, segs, _ = D.read_files(paths, {})
            D.attach_segments(m, segs)
        self.assertEqual([s["cm"] for s in m["D-11AA"]["segs"]], [12.5])
        self.assertEqual([s["cm"] for s in m["D-99ZZ"]["segs"]], [95.0])
        self.assertEqual(m["AB-12"]["name"], "Al Sample")

    def test_a_match_id_listed_twice_is_one_person(self):
        ops, _, rep = run("myheritage", ["myheritage-matches.csv", "myheritage-matches.csv"])
        self.assertEqual(rep["matches"], 4)
        self.assertIn("D-11AA", rep["cols"][fx("myheritage-matches.csv")]["duplicate_ids"])

    def test_same_name_matches_keep_their_ids_when_the_export_is_reordered(self):
        with tempfile.TemporaryDirectory() as d:
            a, = self.files(d, a="Full Name,Shared cM\nLee Ward,22\nLee Ward,30\n")
            m, _, _ = D.read_files([a], {})
        self.assertEqual((m["name-lee-ward"]["cm"], m["name-lee-ward-2"]["cm"]), (30, 22))


class Identity(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def test_a_match_from_another_site_with_a_close_cm_is_the_same_person(self):
        p, n = marked("I0050", "Tom", "Sample", "myheritage", "D-11AA", 48.0)
        snap = write_tree(self.d.name, tree([p], [n]))
        ops, _, rep = run("ftdna", ["ftdna-matches.csv", "ftdna-segments.csv"], snapshot=snap)
        self.assertFalse([o for o in ops if o["op"] == "add_person" and o["first_name"] == "Tom"])
        self.assertIn({"op": "add_association", "key": "dna-ftdna-name-tom-sample-I0001-assoc", "ruling": "R-1",
                       "person": "I0001", "target": "I0050", "relation": "DNA"}, ops)
        self.assertEqual(rep["cross_site"][0]["person"], "I0050")

    def test_close_cm(self):
        self.assertTrue(D.close_cm(48.0, 41.5))
        self.assertTrue(D.close_cm(200.0, 150.0))   # 50 cM apart, within 30 % of 200
        self.assertTrue(D.close_cm(5.0, 15.0))      # small matches: 10 cM either way
        self.assertFalse(D.close_cm(200.0, 20.0))
        self.assertFalse(D.close_cm(None, 20.0))

    def test_same_name_far_cm_is_another_person_named_as_namesake(self):
        p, n = marked("I0051", "Jane", "Doe", "myheritage", "D-22BB", 20.0)   # FTDNA's Jane Doe shares 200 cM
        snap = write_tree(self.d.name, tree([p], [n]))
        ops, _, rep = run("ftdna", ["ftdna-matches.csv"], snapshot=snap)
        jane = next(o for o in ops if o["op"] == "add_person" and o["first_name"] == "Jane")
        self.assertEqual(sorted(jane["namesake_of"]), ["I0002", "I0051"])
        self.assertEqual(rep["cross_site"], [])

    def test_two_close_candidates_are_ambiguous_and_make_a_new_person(self):
        p1, n1 = marked("I0060", "Tom", "Sample", "myheritage", "D-1", 45.0)
        p2, n2 = marked("I0061", "Tom", "Sample", "gedmatch", "ZZ1", 40.0)
        snap = write_tree(self.d.name, tree([p1, p2], [n1, n2]))
        ops, _, rep = run("ftdna", ["ftdna-matches.csv"], snapshot=snap)
        self.assertEqual(rep["cross_ambiguous"], [{"match": "name-tom-sample", "people": ["I0060", "I0061"]}])
        self.assertTrue([o for o in ops if o["op"] == "add_person" and o["first_name"] == "Tom"])

    def test_a_rerun_of_the_same_site_points_at_the_person_it_made(self):
        p, n = marked("I0070", "Ruth", "Example", "myheritage", "D-33CC", 130.5)
        snap = write_tree(self.d.name, tree([p], [n]))
        ops, _, rep = run("myheritage", ["myheritage-matches.csv"], snapshot=snap, tested="I0002", kit="kit-b")
        self.assertFalse([o for o in ops if o["op"] == "add_person" and o["first_name"] == "Ruth"])
        self.assertIn({"match": "D-33CC", "person": "I0070", "by": "same-site"}, rep["in_tree"])

    def test_id_map_and_the_tested_person_as_a_match(self):
        ops, _, rep = run("myheritage", ["myheritage-matches.csv"], id_map={"D-11AA": "I0002", "D-33CC": "I0001"})
        self.assertIn("I0002", [o.get("target") for o in ops])
        self.assertEqual(rep["self"], ["D-33CC"])
        self.assertFalse([o for o in ops if o.get("person") == o.get("target") is not None])
        self.assertEqual(len([o for o in ops if o["op"] == "add_person"]), 2)

    def test_id_map_file(self):
        p = os.path.join(self.d.name, "map.tsv")
        with open(p, "w") as fh:
            fh.write("# site\tmatch\tid\nftdna\tTom Sample\tI0009\nmyheritage\tD-1\tI0008\n")
        self.assertEqual(D.read_id_map(p, "ftdna"), {"Tom Sample": "I0009", "name:tom sample": "I0009"})
        with open(p, "a") as fh:
            fh.write("ftdna\tbad\n")
        with self.assertRaises(D.BadInput):
            D.read_id_map(p, "ftdna")


class SegmentNote(unittest.TestCase):
    def test_note_is_the_string_gramps_web_accepted(self):
        with open(fx("parser-accepted.json")) as fh:
            rec = json.load(fh)
        _, groups, _ = run("myheritage", ["myheritage-matches.csv", "myheritage-segments.csv"])
        tom = next(g for g in groups if g[0].get("first_name") == "Tom")
        self.assertEqual(tom[3]["text"], rec["request"]["string"])
        ours = D.parse_like_gramps_web(rec["request"]["string"])
        theirs = [{k: s[k] for k in ("chromosome", "start", "stop", "cM", "SNPs")} for s in rec["response"]]
        self.assertEqual(ours, theirs)

    def test_the_offline_reader_refuses_what_the_server_would_misread(self):
        self.assertEqual(D.parse_like_gramps_web("1,2,3,4,5\n7,1,9,12.5,1500"), [])   # numeric header
        self.assertEqual(D.parse_like_gramps_web("Name,Start,End,cM\n7,1,9,12.5"), [])  # no chromosome column


class Files(unittest.TestCase):
    def test_batches_never_split_a_match(self):
        _, groups, _ = run("myheritage", ["myheritage-matches.csv", "myheritage-segments.csv"])
        sizes = [len(g) for g in groups]           # 4, 4, 3, 3
        files = D.blocks(groups, 7)
        self.assertEqual([len(f) for f in files], [4, 7, 3])
        self.assertTrue(all(len(f) <= 7 for f in files))
        self.assertEqual(sum(map(len, files)), sum(sizes))

    def cli(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = D.main(list(args))
        return code, err.getvalue()

    def test_cli_writes_split_files_refuses_overwrite_and_bad_tested(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "c.jsonl")
            base = ["--site", "myheritage", "--snapshot", fx("tree.json"), "--out", out,
                    fx("myheritage-matches.csv"), fx("myheritage-segments.csv")]
            code, err = self.cli(*base, "--tested", "I0001", "--batch", "7")
            self.assertEqual(code, 0, err)
            self.assertEqual(sorted(os.listdir(d)), ["c-01.jsonl", "c-02.jsonl", "c-03.jsonl"])
            self.assertIn("columns not read: ", err)
            code, err = self.cli(*base, "--tested", "I0001", "--batch", "7")
            self.assertEqual(code, 2)
            self.assertIn("--force", err)
            code, err = self.cli(*base, "--tested", "I0999", "--force")
            self.assertEqual((code, "not in the snapshot" in err), (2, True))


@unittest.skipIf(FakeGrampsWeb is None and not os.environ.get("CI"), "gwt-apply not importable")   # CI must run it
class EndToEnd(unittest.TestCase):
    """Two sites into one tree through gwt-apply: a match on both becomes one person with two notes."""

    def setUp(self):
        self.fake = FakeGrampsWeb()
        for first, sur in (("Ana", "Example"), ("Jane", "Doe")):
            self.fake.add("people", {"gender": 0, "primary_name": {"first_name": first, "surname_list": [
                {"surname": sur, "primary": True}]}})
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def dump(self):
        p = os.path.join(self.d.name, "dump-%d.json.gz" % len(os.listdir(self.d.name)))
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            json.dump({"objects": {ns: list(self.fake.store[ns].values()) for ns in ("people", "notes")}}, fh)
        return p

    def apply(self, ops):
        self.client = Client("https://gw.example", "u", "pw", dry_run=False, session=self.fake)  # one run, one client
        self.client.login()
        self.assertEqual(validate(ops), [])
        self.assertEqual(preflight(ops, self.client), [])
        res = Applier(self.client, retries=1, retry_wait=0).run(copy.deepcopy(ops))
        self.assertEqual([(r.key, r.status) for r in res if r.status not in ("done", "skip")], [])

    def people(self, first, sur):
        return [p for p in self.fake.store["people"].values() if p["primary_name"]["first_name"] == first
                and p["primary_name"]["surname_list"][0]["surname"] == sur]

    def test_myheritage_then_ftdna(self):
        tested = "I0001"
        mh, _, _ = run("myheritage", ["myheritage-matches.csv", "myheritage-segments.csv"], snapshot=self.dump(),
                       tested=tested)
        self.apply(mh)
        ft, _, rep = run("ftdna", ["ftdna-matches.csv", "ftdna-segments.csv"], snapshot=self.dump(), tested=tested)
        self.apply(ft)
        toms = self.people("Tom", "Sample")
        self.assertEqual(len(toms), 1)
        self.assertEqual(len(self.people("Jane", "Doe")), 3)        # the tree's, MyHeritage's (20 cM), FTDNA's (200 cM)
        ana = self.people("Ana", "Example")[0]
        dna = [r for r in ana["person_ref_list"] if r["rel"] == "DNA" and r["ref"] == toms[0]["handle"]]
        self.assertEqual(len(dna), 1)
        notes = {n["handle"]: n["text"]["string"] for n in self.fake.store["notes"].values()}
        self.assertEqual(len([h for h in dna[0]["note_list"] if notes[h].startswith("Chromosome,")]), 2)
        self.assertEqual(sorted(notes[h].split()[1] for h in toms[0]["note_list"]), ["site=ftdna", "site=myheritage"])
        again, _, _ = run("ftdna", ["ftdna-matches.csv", "ftdna-segments.csv"], snapshot=self.dump(), tested=tested)
        self.assertFalse([o for o in again if o["op"] == "add_person"])     # a rerun finds every one it made


if __name__ == "__main__":
    unittest.main()
