import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "check"))
import gwt_prove as g  # noqa: E402


def D(y=0, m=0, d=0, mod=0, q=0, text=""):
    return {"dateval": [d, m, y, False], "modifier": mod, "quality": q, "calendar": 0, "text": text}


class T:
    """A tiny synthetic tree: people are (id, gender, birth date, death date)."""

    def __init__(self):
        self.objs = {k: {} for k in ("people", "families", "events", "notes", "citations", "sources")}
        self.n = 0

    def person(self, gid, gender, birth=None, death=None):
        refs = []
        for typ, dt in (("Birth", birth), ("Death", death)):
            if dt:
                self.n += 1
                h = f"e{self.n}"
                self.objs["events"][h] = {"gramps_id": f"E{self.n}", "type": typ, "date": dt, "citation_list": ["c"]}
                refs.append({"ref": h, "role": "Primary"})
        self.objs["people"][gid] = {"gramps_id": gid, "gender": gender, "event_ref_list": refs, "birth_ref_index": 0,
                                    "death_ref_index": -1, "primary_name": {"first_name": gid, "surname_list": [{"surname": "X"}]},
                                    "note_list": [], "citation_list": []}

    def family(self, fid, father, mother, kids):
        self.objs["families"][fid] = {"gramps_id": fid, "father_handle": father, "mother_handle": mother, "event_ref_list": [],
                                      "citation_list": [], "note_list": [],
                                      "child_ref_list": [{"ref": k, "frel": "Birth", "mrel": "Birth", "note_list": [], "citation_list": []} for k in kids]}


def rules(t):
    return {f["rule"] for f in g.Prover(t, include_living=True).run()}


def test_dates_become_intervals():
    assert g.span(D(1900)).precision == "year"
    assert g.span(D(1900, 2)).hi - g.span(D(1900, 2)).lo == 27
    assert g.span(D(1900, mod=3)).firm is False
    assert g.span(D(0)) is None
    assert g.span(D(0, mod=6, text="1878")).lo == g.span(D(1878)).lo
    assert g.span(D(0, mod=6, text="free text")) is None


def test_father_too_young_and_clean_tree():
    t = T()
    t.person("F", 1, D(1885, 8, 16)); t.person("M", 0, D(1870)); t.person("K", 0, D(1896, 1, 18))
    t.family("F1", "F", "M", ["K"])
    assert "father-too-young" in rules(t)
    t2 = T()
    t2.person("F", 1, D(1860)); t2.person("M", 0, D(1865)); t2.person("K", 0, D(1896, 1, 18))
    t2.family("F1", "F", "M", ["K"])
    assert rules(t2) == set()


def test_vague_dates_do_not_make_a_firm_finding():
    t = T()
    t.person("F", 1, D(1885, mod=3)); t.person("M", 0, D(1870)); t.person("K", 0, D(1893))
    t.family("F1", "F", "M", ["K"])
    fs = g.Prover(t, include_living=True).run()
    assert fs == [] or all(not f["firm"] for f in fs)


def test_child_after_mother_death_but_posthumous_father_ok():
    t = T()
    t.person("F", 1, D(1860), D(1895, 6)); t.person("M", 0, D(1865), D(1894)); t.person("K", 0, D(1895, 12))
    t.family("F1", "F", "M", ["K"])
    r = rules(t)
    assert "child-after-mother-death" in r and "child-after-father-death" not in r


def test_death_before_birth_and_own_ancestor():
    t = T()
    t.person("A", 1, D(1900), D(1890)); t.person("B", 0, D(1930)); t.person("C", 1, D(1960))
    t.family("F1", "A", "B", ["C"]); t.family("F2", "C", None, ["A"])
    r = rules(t)
    assert "death-before-birth" in r and "own-ancestor" in r


def test_siblings_too_close_but_twins_pass():
    t = T()
    t.person("F", 1, D(1860)); t.person("M", 0, D(1865))
    t.person("K1", 0, D(1890, 1, 1)); t.person("K2", 0, D(1890, 3, 1)); t.person("K3", 1, D(1892, 5, 1)); t.person("K4", 0, D(1892, 5, 1))
    t.family("F1", "F", "M", ["K1", "K2", "K3", "K4"])
    fs = [f for f in g.Prover(t, include_living=True).run() if f["rule"] == "siblings-too-close"]
    assert len(fs) == 1


def test_possibly_living_people_are_held_back():
    t = T()
    t.person("F", 1, D(2015)); t.person("M", 0, D(1990)); t.person("K", 0, D(2020))
    t.family("F1", "F", "M", ["K"])
    assert g.Prover(t).run() == []


def test_link_kinds_follow_the_trees_own_evidence():
    t = T()
    t.person("F", 1, D(1860)); t.person("M", 0, D(1865)); t.person("K", 0, D(1890))
    t.family("F1", "F", "M", ["K"])
    note = ("EVIDENCIA RELACION R1 | hijo/a K | Padre biológico | progenitor F | citas canon C1 | "
            "Estado_hechos canon (verbatim): Hipótesis | Confianza canon (verbatim): Baja | Notas canon (verbatim): x")
    t.objs["notes"]["n1"] = {"text": {"string": note}}
    t.objs["families"]["F1"]["note_list"] = ["n1"]
    pr = g.Prover(t, include_living=True)
    assert pr.link_grades()[0]["kind"] == "hypothesis"
    assert len(pr.overstated()) == 1
    t.objs["families"]["F1"]["child_ref_list"][0]["note_list"] = ["n2"]
    t.objs["notes"]["n2"] = {"text": {"string": "[probable] only the name"}}
    assert g.Prover(t, include_living=True).overstated() == []


def test_no_note_means_unrecorded_not_direct():
    t = T()
    t.person("F", 1, D(1860)); t.person("M", 0, D(1865)); t.person("K", 0, D(1890))
    t.family("F1", "F", "M", ["K"])
    assert g.Prover(t, include_living=True).link_grades()[0]["kind"] == "unrecorded"


def test_legal_parent_hint_and_cli(tmp_path, capsys):
    import json
    t = T()
    t.person("F", 1, D(1885, 8, 16)); t.person("M", 0, D(1870)); t.person("K", 0, D(1896, 1, 18))
    t.family("F1", "F", "M", ["K"])
    t.objs["notes"]["n1"] = {"text": {"string": "Paternidad por legitimación en el acta de matrimonio"}}
    t.objs["families"]["F1"]["note_list"] = ["n1"]
    fs = g.Prover(t, include_living=True).run()
    assert fs[0]["note"]
    dump = {k: [dict(o, handle=h) for h, o in v.items()] for k, v in t.objs.items()}
    dump.update({"media": [], "repositories": [], "places": [], "tags": []})
    f = tmp_path / "d.json"
    f.write_text(json.dumps(dump))
    assert g.main([str(f), "--include-living"]) == 1
    assert "legitimation" in capsys.readouterr().out
    assert g.main([str(tmp_path / "missing.json")]) == 2
