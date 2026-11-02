"""The framework: exit codes, the CLI, and --prove, the positive control for every rule."""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from gwt_lint import cli
from gwt_lint import framework as F

HERE = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.dirname(HERE)
ROOT = os.path.dirname(LINT)
EXAMPLE = [os.path.join(ROOT, "examples", "changes.jsonl"), "--snapshot", os.path.join(ROOT, "examples", "tree.json.gz")]
FIX = os.path.join(LINT, "fixtures", "tree")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_prove_passes_on_the_example_list():
    code, out, _ = run(["--prove"] + EXAMPLE)
    assert code == 0, out
    assert out.count("proved ") == len(F.registry())


def test_prove_passes_on_every_fixture_list():
    for name in sorted(os.listdir(FIX)):
        if name.endswith(".jsonl"):
            code, out, _ = run(["--prove", os.path.join(FIX, name), "--snapshot", os.path.join(FIX, "tree.json"),
                                "--config", os.path.join(FIX, "lint.toml")])
            assert code == 0, (name, out)


@pytest.mark.parametrize("rule", sorted(F.registry()))
def test_prove_fails_when_a_rule_stops_detecting(rule, monkeypatch):
    # the positive control for the framework: a rule that finds nothing must fail --prove
    cls = type(F.registry()[rule])
    monkeypatch.setattr(cls, "check", lambda self, ctx: [])
    code, out, _ = run(["--prove"] + EXAMPLE)
    assert code == 1
    assert f"FAILED {rule}: its mutant did not fire" in out


def test_prove_fails_when_the_mutant_fires_only_what_the_list_already_fires():
    rule = F.registry()["count"]
    ops = [{"op": "add_note", "key": "n", "_line": 1, "text": "The tree has 12 people named Vega.",
            "attach": [{"person": "I1"}]}]
    ctx = F.Context(ops, F.Tree.load(os.path.join(FIX, "tree.json")))

    class Same(type(rule)):
        def mutate(self, ctx):
            return F.Mutant(list(ctx.ops), "count-in-text")
    passed, line = F.prove(Same(), ctx)
    assert not passed and "(1 on the list, 1 on the mutant" in line


def test_prove_fails_when_the_mutant_fires_another_code():
    rule = F.registry()["count"]

    class Other(type(rule)):
        codes = {**type(rule).codes, "count-other": "x"}

        def mutate(self, ctx):
            m = super().mutate(ctx)
            return F.Mutant(m.ops, "count-other")
    ctx = F.Context([{"op": "add_tag", "key": "t", "_line": 1, "target": {"person": "I1"}, "name": "x"}],
                    F.Tree.load(os.path.join(FIX, "tree.json")))
    passed, line = F.prove(Other(), ctx)
    assert not passed and "fired count-in-text" in line


def test_prove_fails_when_the_rule_has_nothing_to_check_on_its_mutant():
    rule = F.registry()["scope"]

    class Blind(type(rule)):
        def mutate(self, ctx):
            m = super().mutate(ctx)
            return F.Mutant(m.ops, m.code, {"scope": {"roots": []}})
    ctx = F.Context([{"op": "add_tag", "key": "t", "_line": 1, "target": {"person": "I1"}, "name": "x"}],
                    F.Tree.load(os.path.join(FIX, "tree.json")))
    passed, line = F.prove(Blind(), ctx)
    assert not passed and "nothing to check on its own mutant" in line


def test_nothing_to_check_wins_over_findings():
    findings = F.Result("a", "findings", [F.Finding("a", "x", "k", 1, "m")])
    assert F.exit_code([findings]) == 1
    assert F.exit_code([findings, F.Result("b", "nothing", reason="r")]) == 2
    assert F.exit_code([F.Result("a", "ok")]) == 0


def test_text_output_prints_codes_and_the_exit_line():
    code, out, _ = run([os.path.join(FIX, "count-finding.jsonl"), "--snapshot", os.path.join(FIX, "tree.json"),
                        "--rules", "count,living"])
    assert code == 1
    assert "count-in-text      op 'n-ana' line 1: counts what the tree holds" in out
    assert out.rstrip().endswith("exit 1: findings (2 findings from 2 rules)")


def test_rules_from_the_config_and_unknown_rules(tmp_path):
    cfg = tmp_path / "lint.toml"
    cfg.write_text('rules = ["count"]\n')
    code, out, _ = run([os.path.join(FIX, "count-finding.jsonl"), "--snapshot", os.path.join(FIX, "tree.json"),
                        "--config", str(cfg), "--json"])
    assert code == 1 and [r["rule"] for r in json.loads(out)["rules"]] == ["count"]
    code, _, err = run(EXAMPLE + ["--rules", "count,nope"])
    assert code == 2 and "no rule nope" in err


def test_unreadable_inputs_exit_2(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"op": "add_tag"}\nnot json\n')
    assert run([str(bad), "--snapshot", EXAMPLE[2]])[0] == 2
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n")
    assert run([str(empty), "--snapshot", EXAMPLE[2]])[0] == 2
    assert run([EXAMPLE[0], "--snapshot", str(tmp_path / "missing.json")])[0] == 2
    assert run([EXAMPLE[0]])[0] == 2


def test_config_folders_are_relative_to_the_config_file():
    cfg = cli.load_config(os.path.join(FIX, "lint.toml"))
    assert cfg["overlap"]["open"] == os.path.join(os.path.realpath(FIX), "open")


def test_the_snapshot_reads_both_shapes():
    dump = F.Tree.load(os.path.join(FIX, "tree.json"))           # gwt-check dump: objects.people
    snap = F.Tree.load(EXAMPLE[2])                                # gwt-apply snapshot: objects.Person, gzip
    assert dump.gids["Person"]["I1"] == "hI1" and snap.gids["Person"]["I0001"] == "hI0001"
    assert dump.tag_names == {"htagweak": "weak"}


def test_refs_name_every_kind_of_reference():
    op = {"op": "add_citation", "key": "c", "source": "S1", "page": "p",
          "attach": [{"child_ref": {"family": "F2", "child": "I6"}}, {"event": "E1b"},
                     {"association": {"person": "I10", "target": "I12"}}]}
    assert set(F.refs(op)) == {("Citation", "key:c"), ("Source", "S1"), ("ChildRef", ("F2", "I6")),
                               ("Event", "E1b"), ("Association", ("I10", "I12"))}
    merge = {"op": "merge_person", "key": "m", "winner": "I1", "loser": "I4"}
    assert set(F.refs(merge)) == {("Person", "I1"), ("Person", "I4")}


def test_the_wrapper_runs_from_a_clone():
    r = subprocess.run([sys.executable, os.path.join(LINT, "gwt-lint"), "--prove"] + EXAMPLE,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_default_run_never_asks_for_a_field_gwt_apply_refuses(tmp_path):
    # `expect` on detach and remove_tag is refused by gwt-apply until it accepts it, so no rule may demand it
    lst = tmp_path / "detach.jsonl"
    lst.write_text('{"op": "detach", "key": "d", "ruling": "R-1", "target": {"person": "I1"}, '
                   '"object": {"note": "N2"}}\n')
    code, out, _ = run([str(lst), "--snapshot", os.path.join(FIX, "tree.json"), "--config",
                        os.path.join(FIX, "lint.toml")])
    assert code == 0, out
    assert "expect" not in out


def test_a_json_array_or_ops_object_list_is_read_like_gwt_apply_reads_it(tmp_path):
    op = {"op": "add_note", "key": "n", "ruling": "R-1", "text": "Witness at the wedding.", "attach": [{"person": "I1"}]}
    for name, body in (("arr.json", [op]), ("obj.json", {"ops": [op]})):
        lst = tmp_path / name
        lst.write_text(json.dumps(body))
        code, out, err = run([str(lst), "--snapshot", os.path.join(FIX, "tree.json"), "--rules", "scope", "--root", "I1"])
        assert code == 0, (name, out, err)


def test_a_list_gwt_apply_would_refuse_exits_2(tmp_path):
    lst = tmp_path / "bad.jsonl"
    lst.write_text('{"op": "add_note", "key": "n", "ruling": "R-1", "text": "x", "attach": [{"person": "I1"}], '
                   '"colour": "red"}\n')
    code, _, err = run([str(lst), "--snapshot", os.path.join(FIX, "tree.json"), "--rules", "count"])
    assert code == 2 and "unknown fields ['colour']" in err


def test_unreadable_bytes_and_bad_settings_exit_2_not_1(tmp_path):
    latin = tmp_path / "latin.jsonl"
    latin.write_bytes(b'{"op": "add_note", "key": "n", "ruling": "R", "text": "Jos\xe9", "attach": []}\n')
    assert run([str(latin), "--snapshot", os.path.join(FIX, "tree.json")])[0] == 2
    gz = tmp_path / "cut.json.gz"
    gz.write_bytes(b"\x1f\x8b\x08\x00garbage")
    assert run([EXAMPLE[0], "--snapshot", str(gz)])[0] == 2
    for bad in ('[living]\nyears = "ninety"\n', '[weak]\ntag = ["weak", 3]\n', 'overlap = "x"\n',
                '[protect]\nids = ["I10"]\nlocales = ["xx"]\n'):
        cfg = tmp_path / "lint.toml"
        cfg.write_text(bad)
        code, out, err = run([os.path.join(FIX, "count-clean.jsonl"), "--snapshot", os.path.join(FIX, "tree.json"),
                              "--config", str(cfg)])
        assert code == 2, (bad, out, err)


def test_a_one_name_setting_may_be_a_bare_string(tmp_path):
    cfg = tmp_path / "lint.toml"
    cfg.write_text('rules = "scope"\n[scope]\nroots = "I1"\n')
    code, out, err = run([os.path.join(FIX, "scope-clean.jsonl"), "--snapshot", os.path.join(FIX, "tree.json"),
                          "--config", str(cfg)])
    assert code == 0, out + err


def test_json_output_reports_an_input_error_on_stdout(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n")
    code, out, _ = run([str(empty), "--snapshot", os.path.join(FIX, "tree.json"), "--json"])
    doc = json.loads(out)
    assert code == 2 and doc["exit"] == 2 and "no ops" in doc["error"]
