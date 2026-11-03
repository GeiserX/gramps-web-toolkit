"""The evidence rules on invented saved records: a clean list, a list with each code, NOREC and --prove."""
import io
import json
import os
import shutil
import unicodedata
from contextlib import redirect_stdout

import pytest

from gwt_lint import cli
from gwt_lint import framework as F
from gwt_lint import rules_evidence as E

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "fixtures", "evidence")
TREE = os.path.join(FIX, "tree.json")
CONFIG = os.path.join(FIX, "lint.toml")
RECORDS = os.path.join(FIX, "records")
RULES = ["quote", "principal", "role", "placement"]
FS = "https://www.familysearch.org/ark:/61903/"


def lint(lst, rule, *extra, config=CONFIG):
    """(exit, {code: [op keys]}, the rule's result) of one rule on one list, read from --json."""
    args = [lst if os.path.isabs(lst) else os.path.join(FIX, lst), "--snapshot", TREE, "--rules", rule, "--json"]
    args += list(extra) + (["--config", config] if config else [])
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(args)
    doc = json.loads(out.getvalue())
    res = doc["rules"][0]
    codes = {}
    for f in res["findings"]:
        codes.setdefault(f["code"], []).append(f.get("key", ""))
    return code, codes, res


def write(tmp_path, ops, name="list.jsonl"):
    p = tmp_path / name
    p.write_text("".join(json.dumps({"ruling": "R-1", **o}, ensure_ascii=False) + "\n" for o in ops), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("rule", RULES)
def test_clean_list_exits_0(rule):
    code, codes, res = lint(f"{rule}-clean.jsonl", rule)
    assert (code, codes) == (0, {}), res


def test_quote_findings():
    code, codes, _ = lint("quote-finding.jsonl", "quote")
    assert code == 1
    assert codes == {"quote-notfound": ["n-accent", "n-invented", "n-other-record"], "quote-image": ["n-image"]}


def test_principal_findings():
    code, codes, res = lint("principal-finding.jsonl", "principal")
    assert code == 1
    # the godfather's ark sits in the note text, not in a page: it is read all the same
    assert codes == {"principal-nonprincipal": ["c-father", "n-godfather", "c-cand"], "principal-event": ["c-death"]}
    fixes = {f["key"]: f.get("fix", "") for f in res["findings"]}
    assert fixes["c-father"] == FS + "1:1:QZZ9-AAA", "the fix is the principal's ark"


def test_role_findings():
    code, codes, _ = lint("role-finding.jsonl", "role")
    assert code == 1
    assert codes == {"role-mismatch": ["n-father", "l-godfather"], "role-samesex": ["c-pedro"]}


def test_placement_findings():
    code, codes, _ = lint("placement-finding.jsonl", "placement")
    assert code == 1
    assert codes == {"placement-town": ["e-bap-town", "u-bap-town"], "placement-civil": ["e-birth-church"],
                     "placement-parish": ["e-bap-cristo", "e-bap-maria"]}


@pytest.mark.parametrize("rule", RULES)
def test_no_records_is_nothing_to_check(rule, tmp_path):
    code, _, res = lint(f"{rule}-clean.jsonl", rule, config=None)
    assert (code, res["status"]) == (2, "nothing") and "--records" in res["reason"]
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, res = lint(f"{rule}-clean.jsonl", rule, "--records", str(empty), config=None)
    assert (code, res["status"]) == (2, "nothing") and "no saved records" in res["reason"]


@pytest.mark.parametrize("rule", RULES)
def test_an_ark_never_saved_is_norec_exit_2(rule, tmp_path):
    ops = [{"op": "add_event", "key": "e", "person": "I1", "type": "Baptism", "place": "P2"},
           {"op": "add_citation", "key": "c", "source": "S1", "page": FS + "1:1:QZZ0-NEW", "attach": [{"event": "key:e"}]},
           {"op": "add_note", "key": "n", "text": f"Godparent: «bauticé solemnemente a una niña» {FS}1:1:QZZ0-NEW",
            "attach": [{"citation": "key:c"}]}]
    code, _, res = lint(write(tmp_path, ops), rule)
    assert (code, res["status"]) == (2, "nothing"), res
    assert "QZZ0-NEW" in res["reason"] and "NOREC" in res["reason"]


def test_quote_matches_after_nfc_only(tmp_path):
    decomposed = unicodedata.normalize("NFD", "bauticé solemnemente a una niña que nació")
    assert decomposed != unicodedata.normalize("NFC", decomposed)
    ops = [{"op": "add_note", "key": "n", "text": f"«{decomposed}» {FS}1:1:QZZ9-AAA", "attach": [{"person": "I1"}]},
           {"op": "add_note", "key": "m", "text": f"«BAUTICÉ SOLEMNEMENTE A UNA NIÑA» {FS}1:1:QZZ9-AAA",
            "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "quote")[1] == {"quote-notfound": ["m"]}, "case is not folded; NFC is"


def test_a_reading_row_needs_an_image_sha_and_a_reader(tmp_path):
    rows = tmp_path / "readings.tsv"
    rows.write_text("nota marginal de difícil lectura aquí\t3f9a0c1b2d4e\t\n"
                    "estas palabras no tienen lectura firmada\tnot-a-sha\tA. Reader\n", encoding="utf-8")
    ops = [{"op": "add_note", "key": "n", "text": f"«nota marginal de difícil lectura aquí» {FS}3:1:QZZ9-IMG2",
            "attach": [{"person": "I1"}]},
           {"op": "add_note", "key": "m", "text": f"«estas palabras no tienen lectura firmada» {FS}3:1:QZZ9-IMG2",
            "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "quote", "--readings", str(rows))[1] == {"quote-image": ["n", "m"]}


def test_records_from_the_config_are_read_relative_to_it(tmp_path):
    shutil.copytree(RECORDS, tmp_path / "saved")
    (tmp_path / "lint.toml").write_text('records = "saved"\n', encoding="utf-8")
    code, codes, _ = lint("principal-finding.jsonl", "principal", config=str(tmp_path / "lint.toml"))
    assert code == 1 and "principal-nonprincipal" in codes


def test_records_setting_must_be_a_text(tmp_path):
    (tmp_path / "bad.toml").write_text("records = 3\n", encoding="utf-8")
    with pytest.raises(F.InputError):
        cli.load_config(str(tmp_path / "bad.toml"))


def test_an_override_needs_its_quote_on_the_page(tmp_path):
    cfg = tmp_path / "lint.toml"
    cfg.write_text(f'records = "{RECORDS}"\n[placement.overrides.e-bap-pedro]\n'
                   'place = "Ermita del Santo Cristo del Valle"\nquote = "en la ermita junto al río"\n', encoding="utf-8")
    code, codes, _ = lint("placement-clean.jsonl", "placement", config=str(cfg))
    assert codes == {"placement-override": ["e-bap-pedro"], "placement-parish": ["e-bap-pedro"]}


@pytest.mark.parametrize("church,town,orig,match", [
    ("Parroquia de Santa Eulalia", "Villazul", "Parroquia de Santa Eulalia, Villazul", True),
    ("Parroquia de Sta. Eulalia", "Villazul", "Santa Eulalia, Villazul", True),          # the locale's abbreviation
    ("Ermita del Santo Cristo del Valle", "Villazul", "Santísimo Cristo del Valle", False),  # titles count
    ("Iglesia de Santa María", "Villazul", "Parroquia de Santa María, Villazul", True),
    ("Iglesia de Santa María", "Villazul", "Santa María, Otraparte", False),           # generic: needs the town
    ("Iglesia parroquial", "Villazul", "Iglesia parroquial de Villazul", False),       # no dedication at all
])
def test_church_matches(church, town, orig, match):
    assert E.church_matches(church, town, [orig], ["es", "en"]) is match


def test_candidate_rows_and_fscache_entries_are_records():
    recs = E.Records(RECORDS)
    rec, persona = recs.find("1:1:QZZ7-DDA")
    assert (persona["principal"], E.relation(rec, persona)) == (False, "father")
    rec, persona = recs.find("1:1:QZZ6-CCB")
    assert E.relation(rec, persona) == "spouse"
    assert any("cura propio" in t for t in recs.texts_of("3:1:QZZ9-IMG1")), "a .txt binds to the ark on its first line"


def test_prove_passes_on_every_evidence_list():
    for name in sorted(os.listdir(FIX)):
        if name.endswith(".jsonl"):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.main(["--prove", os.path.join(FIX, name), "--snapshot", TREE, "--config", CONFIG,
                                 "--rules", ",".join(RULES)])
            assert code == 0, (name, out.getvalue())
            assert out.getvalue().count("proved ") == 4


@pytest.mark.parametrize("rule", RULES)
def test_prove_fails_when_an_evidence_rule_stops_detecting(rule, monkeypatch):
    cls = type(F.registry()[rule])
    monkeypatch.setattr(cls, "check", lambda self, ctx: [])
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["--prove", os.path.join(FIX, f"{rule}-clean.jsonl"), "--snapshot", TREE, "--config", CONFIG,
                         "--rules", rule])
    assert code == 1 and f"FAILED {rule}: its mutant did not fire" in out.getvalue()


def test_each_mutant_fires_its_own_code():
    ctx = F.Context([], F.Tree.load(TREE), cli.load_config(CONFIG), records=RECORDS)
    want = {"quote": "quote-notfound", "principal": "principal-nonprincipal", "role": "role-mismatch",
            "placement": "placement-town"}
    for name, code in want.items():
        rule = F.registry()[name]
        m = rule.mutate(ctx)
        assert m.code == code
        assert [f.code for f in rule.check(ctx.but(m.ops))] == [code], name


def test_the_full_record_wins_over_the_candidate_row_saved_beside_it():
    # gwt-read writes <key>.json and <key>.candidates.json; the row sorts first but has no facts or relationships
    assert os.path.exists(os.path.join(RECORDS, "baptism-ana.candidates.json"))
    rec, persona = E.Records(RECORDS).find("1:1:QZZ9-AAA")
    assert persona["facts"] == {"Birth", "Christening"} and len(rec["rels"]) == 2


def test_a_marriage_fact_on_the_couple_relationship_backs_a_marriage(tmp_path):
    ops = [{"op": "add_event", "key": "e-mar", "family": "F2", "type": "Marriage"},
           {"op": "add_citation", "key": "c-mar", "source": "S1", "page": FS + "1:1:QZZ6-CCA",
            "attach": [{"event": "key:e-mar"}]}]
    assert lint(write(tmp_path, ops), "principal")[:2] == (0, {})


def test_a_record_file_of_another_shape_is_skipped_not_a_crash(tmp_path):
    shutil.copytree(RECORDS, tmp_path / "saved")
    (tmp_path / "saved" / "zy.json").write_text('{"persons": [{"id": "z", "display": "oops", "names": ["x"]}]}',
                                                encoding="utf-8")
    (tmp_path / "saved" / "zz.json").write_text('{"persons": [{"id": "z", "gender": "Male"}]}', encoding="utf-8")
    code, codes, _ = lint("principal-finding.jsonl", "principal", "--records", str(tmp_path / "saved"))
    assert code == 1 and "principal-nonprincipal" in codes


def test_a_symlinked_record_folder_is_read(tmp_path):
    (tmp_path / "saved").mkdir()
    (tmp_path / "saved" / "lane").symlink_to(RECORDS, target_is_directory=True)
    code, codes, _ = lint("principal-finding.jsonl", "principal", "--records", str(tmp_path / "saved"))
    assert code == 1 and "principal-nonprincipal" in codes
