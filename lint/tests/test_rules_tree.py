"""Each tree rule on the invented fixture tree: a clean list, a list with findings, and nothing to check."""
import json
import os
import shutil

import pytest

from gwt_lint import cli
from gwt_lint import framework as F
from gwt_lint import rules_tree as T

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "fixtures", "tree")
TREE = os.path.join(FIX, "tree.json")
CONFIG = os.path.join(FIX, "lint.toml")


def lint(name, rule, *extra, config=CONFIG):
    """(exit, {code: [op keys]}, the rule's result) of one rule on one fixture list, read from --json."""
    args = [os.path.join(FIX, name), "--snapshot", TREE, "--rules", rule, "--json"] + list(extra)
    if config:
        args += ["--config", config]
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(args)
    doc = json.loads(out.getvalue())
    res = doc["rules"][0]
    codes = {}
    for f in res["findings"]:
        codes.setdefault(f["code"], []).append(f.get("key", ""))
    return code, codes, res


RULES = ["scope", "protect", "living", "overlap", "weak", "count"]


@pytest.mark.parametrize("rule", RULES)
def test_clean_list_exits_0(rule):
    code, codes, _ = lint(f"{rule}-clean.jsonl", rule)
    assert (code, codes) == (0, {})


EXPECTED = {
    "scope": {"scope-out": ["e-hugo-res", "n-sara", "a-f5"], "scope-unknown": ["e-ghost"]},
    "protect": {"protect-event": ["e-nora-res"], "protect-date": ["u-nora-birth"], "protect-place": ["u-nora-place"],
                "protect-attach": ["n-nora"], "protect-link": ["l-nora"], "protect-text": ["n-elena"],
                "protect-update": ["u-nora-gender", "m-nora"]},
    "living": {"living-date": ["e-nora-res"], "living-place": ["e-nora-res"],
               "living-marriage": ["e-nora-marr", "e-nora-marr"], "living-quote": ["n-kit"],
               "living-id": ["n-ana-id"]},
    "overlap": {"overlap-open": ["n-elena-2"], "overlap-applied": ["u-luis-birth-again"]},
    "weak": {"weak-untagged": ["p-ana-3", "c-machine", "u-c1"], "weak-inert-tag": ["u-luis"]},
    "count": {"count-in-text": ["n-ana", "e-luis"]},
}


@pytest.mark.parametrize("rule", RULES)
def test_finding_list_exits_1_with_each_code(rule):
    code, codes, _ = lint(f"{rule}-finding.jsonl", rule)
    assert code == 1
    assert codes == EXPECTED[rule]


@pytest.mark.parametrize("rule,why", [("scope", "no roots"), ("protect", "no protected people"),
                                      ("overlap", "no lists to compare with"), ("weak", "no weak tag")])
def test_unset_rule_has_nothing_to_check_and_exits_2(rule, why):
    code, codes, res = lint(f"{rule}-finding.jsonl", rule, config=None)
    assert (code, codes, res["status"]) == (2, {}, "nothing")
    assert why in res["reason"]


def test_a_root_the_snapshot_lacks_is_nothing_to_check():
    code, _, res = lint("scope-clean.jsonl", "scope", "--root", "I404")
    assert code == 2 and "I404 is not in the snapshot" in res["reason"]


def test_living_on_a_tree_without_people_is_nothing_to_check(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text('{"objects": {"people": []}}')
    code = cli.main([os.path.join(FIX, "living-finding.jsonl"), "--snapshot", str(empty), "--rules", "living"])
    assert code == 2


def test_a_missing_open_folder_is_nothing_to_check(tmp_path):
    code, _, res = lint("overlap-finding.jsonl", "overlap", "--open", str(tmp_path / "nope"), config=None)
    assert code == 2 and "no folder" in res["reason"]


def test_scope_paths_name_the_blood_line():
    _, _, res = lint("scope-clean.jsonl", "scope")
    assert "I4 (Luis Prieto Gil): I1 > father I2 > child I4" in res["info"]
    assert "I11 (Mario Ruiz): I1 > child I6 > spouse I11" in res["info"]
    assert "key:p-new-son (Juan Vega Prieto): I1 > child key:p-new-son" in res["info"]


def _view(ops=()):
    return F.View(F.Tree.load(TREE), [dict(o, _line=1) for o in ops])


def _reach(view, root="hI1"):
    return T.Scope.reach(view, [root])[0]


def test_scope_goes_through_adoption_but_never_a_step_link():
    reach = _reach(_view())
    assert "hI9" in reach      # Adopted
    assert "hI8" not in reach  # Stepchild


def test_scope_reaches_collaterals_and_spouses_of_blood_only():
    reach = _reach(_view())
    assert {"hI2", "hI3", "hI4", "hI5", "hI6", "hI10", "hI11"} <= set(reach)
    assert not {"hI7", "hI13", "hI14", "hI12"} & set(reach)  # in-laws, the spouse's father, a DNA match


def test_scope_from_a_child_climbs_through_the_mother_too():
    reach = _reach(_view(), "hI10")
    assert {"hI6", "hI5", "hI1", "hI2"} <= set(reach)


def test_scope_counts_a_child_the_list_links():
    ops = [{"op": "add_person", "key": "kid", "first_name": "A", "surname": "B"},
           {"op": "link_child", "key": "l", "family": "F3", "child": "key:kid"}]
    assert "key:kid" in _reach(_view(ops))


def test_living_counts_a_dna_match_without_dates():
    ops = [{"op": "add_event", "key": "e", "person": "I12", "type": "Residence", "date": "2010-01-02"}]
    ctx = F.Context([dict(o, _line=1) for o in ops], F.Tree.load(TREE), {"living": {"this_year": 2026}})
    assert [f.code for f in T.Living().check(ctx)] == ["living-date"]


def test_living_lets_a_year_alone_through():
    ops = [{"op": "update_event", "key": "u", "event": "E10b", "set": {"date": "1990"}}]
    ctx = F.Context([dict(o, _line=1) for o in ops], F.Tree.load(TREE), {"living": {"this_year": 2026}})
    assert T.Living().check(ctx) == []


def test_living_quote_on_an_existing_note_of_a_living_person():
    tree = F.Tree.load(TREE)
    tree.handles["Person"]["hI10"]["note_list"] = ["hN2"]
    ops = [{"op": "update_note", "key": "u", "note": "N2", "set": {"text": "She said “I was born at home”."},
            "expect": "Ana kept the family bible."}]
    ctx = F.Context([dict(o, _line=1) for o in ops], tree, {"living": {"this_year": 2026}})
    assert [f.code for f in T.Living().check(ctx)] == ["living-quote"]


def test_protect_text_needs_a_life_word_near_the_name():
    names = [("i10", "nora", {"ruiz", "vega"})]
    words = T._life_words(["en"])
    assert T._mentions("Nora Ruiz was born in 1990", names, words)
    assert not T._mentions("Nora Ruiz sent a letter", names, words)
    assert not T._mentions("Nora Ruiz sent a letter about the weather in spring, and much later someone was born",
                           names, words)
    assert T._mentions("I10 died", names, words)


def test_protect_text_reads_the_locale_tables():
    names = [("i10", "nora", {"ruiz"})]
    assert T._mentions("Nora Ruiz nació en la ciudad", names, T._life_words(["es"]))
    assert not T._mentions("Nora Ruiz nació en la ciudad", names, T._life_words(["en"]))


def test_protect_unknown_id_is_a_finding():
    ctx = F.Context([{"op": "add_tag", "key": "t", "_line": 1, "target": {"person": "I1"}, "name": "x"}],
                    F.Tree.load(TREE), {"protect": {"ids": ["I77"]}})
    assert [f.code for f in T.Protect().check(ctx)] == ["protect-unknown"]


def test_weak_child_ref_with_a_probable_mark_in_the_list_counts():
    ops = [{"op": "link_child", "key": "l", "family": "F3", "child": "I12", "probable": True, "probable_reason": "r"},
           {"op": "add_citation", "key": "c", "source": "S2", "page": "p", "confidence": 1,
            "attach": [{"child_ref": {"family": "F3", "child": "I12"}}]}]
    ctx = F.Context([dict(o, _line=1) for o in ops], F.Tree.load(TREE), {"weak": {"tag": "weak"}})
    assert T.Weak().check(ctx) == []


def test_weak_child_ref_without_a_mark_needs_the_tag():
    ops = [{"op": "add_citation", "key": "c", "source": "S1", "page": "p", "confidence": 0,
            "attach": [{"child_ref": {"family": "F2", "child": "I6"}}]}]
    ctx = F.Context([dict(o, _line=1) for o in ops], F.Tree.load(TREE), {"weak": {"tag": "weak"}})
    assert [f.code for f in T.Weak().check(ctx)] == ["weak-untagged"]


def test_weak_tag_on_a_probable_link_is_not_inert():
    ops = [{"op": "link_child", "key": "l", "family": "F3", "child": "I12", "probable": True, "probable_reason": "r",
            "tag": "weak"}, {"op": "link_child", "key": "m", "family": "F3", "child": "I4", "tag": "weak"}]
    ctx = F.Context([dict(o, _line=1) for o in ops], F.Tree.load(TREE), {"weak": {"tag": "weak"}})
    assert [(f.code, f.key) for f in T.Weak().check(ctx)] == [("weak-inert-tag", "m")]


def test_count_ignores_a_year_next_to_the_tree():
    ctx = F.Context([{"op": "add_note", "key": "n", "_line": 1, "text": "Added to the tree in 1998.",
                      "attach": [{"person": "I1"}]}], F.Tree.load(TREE), {})
    assert T.Count().check(ctx) == []


def test_count_words_come_from_the_config():
    op = {"op": "add_note", "key": "n", "_line": 1, "text": "The database holds 12 Prietos.", "attach": [{"person": "I1"}]}
    tree = F.Tree.load(TREE)
    assert T.Count().check(F.Context([op], tree, {})) == []
    assert [f.code for f in T.Count().check(F.Context([op], tree, {"count": {"words": ["database"]}}))] == \
        ["count-in-text"]


def test_overlap_skips_the_list_itself_in_the_open_folder(tmp_path):
    shutil.copy(os.path.join(FIX, "overlap-finding.jsonl"), tmp_path / "mine.jsonl")
    code = cli.main([str(tmp_path / "mine.jsonl"), "--snapshot", TREE, "--rules", "overlap", "--open", str(tmp_path)])
    assert code == 0


def test_overlap_ignores_shared_places_and_sources(tmp_path):
    (tmp_path / "other.jsonl").write_text(json.dumps(
        {"op": "add_citation", "key": "c", "source": "S1", "page": "x", "attach": [{"place": "P1"}]}) + "\n")
    ctx = F.Context([{"op": "add_event", "key": "e", "_line": 1, "person": "I4", "type": "Residence",
                      "place": "P1"}], F.Tree.load(TREE), {"overlap": {"open": str(tmp_path)}})
    assert T.Overlap().check(ctx) == []


def test_namesakes_an_op_says_it_is_not_are_not_touched(tmp_path):
    # namesake_of and new_couple_of name the people and families the new object is NOT, often outside scope
    ops = [{"op": "add_person", "key": "p", "first_name": "Hugo", "surname": "Vega", "allow_namesake": True,
            "namesake_reason": "another man of the same name", "namesake_of": ["I7"], "tag": "weak"},
           {"op": "link_child", "key": "l", "family": "F1", "child": "key:p"},
           {"op": "add_family", "key": "f", "father": "key:p", "allow_new_couple": True,
            "new_couple_reason": "a second marriage", "new_couple_of": ["F5"]}]
    tree = F.Tree.load(TREE)
    ctx = F.Context([dict(o, _line=1) for o in ops], tree, {"scope": {"roots": ["I1"]}})
    assert [f for f in T.Scope().check(ctx) if isinstance(f, F.Finding)] == []
    (tmp_path / "other.jsonl").write_text(json.dumps(
        {"op": "add_note", "key": "n", "text": "x", "attach": [{"person": "I7"}, {"family": "F5"}]}) + "\n")
    ctx = F.Context([dict(o, _line=1) for o in ops], tree, {"overlap": {"open": str(tmp_path)}})
    assert T.Overlap().check(ctx) == []
