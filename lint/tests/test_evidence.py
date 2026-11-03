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
    # a-father: attach_existing hangs the tree citation C2, whose page is the father's persona, on the baptism
    assert codes == {"principal-nonprincipal": ["c-father", "n-godfather", "c-cand", "a-father"],
                     "principal-event": ["c-death"]}
    fixes = {f["key"]: f.get("fix", "") for f in res["findings"]}
    assert fixes["c-father"] == FS + "1:1:QZZ9-AAA", "the fix is the principal's ark"


def test_a_parent_persona_in_a_link_reason_is_nonprincipal_even_with_its_role(tmp_path):
    # the reason is the link's evidence: «names the father Luis Zorvalo <his persona>» must cite the principal
    ops = [{"op": "link_child", "key": "l1", "family": "F1", "child": "I1", "probable": True,
            "probable_reason": f"the baptism names the father Luis Zorvalo: {FS}1:1:QZZ9-AAB"}]
    code, codes, _ = lint(write(tmp_path, ops), "principal")
    assert (code, codes) == (1, {"principal-nonprincipal": ["l1"]})


def test_role_findings():
    code, codes, _ = lint("role-finding.jsonl", "role")
    assert code == 1
    # n-padre-godfather cites the principal and names the godfather as «padre»; l-godfather links Ana into the
    # family of her godfather and her mother
    assert codes == {"role-mismatch": ["n-father", "n-padre-godfather", "l-godfather"], "role-samesex": ["c-lucia"]}


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
    cfg.write_text(f'records = "{RECORDS}"\n[placement.overrides."placement-clean.jsonl".e-bap-pedro]\n'
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


@pytest.mark.parametrize("rule,want", [("quote", {}), ("principal", {}), ("role", {}),
                                       ("placement", {"placement-town": ["e-town"], "placement-civil": ["e-church"]})])
def test_a_list_that_cites_and_quotes_nothing_needs_no_saved_records(rule, want, tmp_path):
    # tree-only lists, DNA imports, geocoding: nothing to compare with a record, so no --records and no exit 2
    ops = [{"op": "add_event", "key": "e-town", "person": "I1", "type": "Baptism", "place": "P1"},
           {"op": "add_event", "key": "e-church", "person": "I1", "type": "Birth", "place": "P2"},
           {"op": "add_note", "key": "n", "text": "Born in the spring, says the family.", "attach": [{"person": "I1"}]}]
    code, codes, res = lint(write(tmp_path, ops), rule, config=None)
    assert (code, codes) == (1 if want else 0, want), res


def test_the_example_list_lints_without_saved_records():
    example = os.path.join(os.path.dirname(os.path.dirname(HERE)), "examples")
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main([os.path.join(example, "changes.jsonl"), "--snapshot", os.path.join(example, "tree.json.gz"),
                         "--rules", ",".join(RULES), "--json"])
    assert all(r["status"] != "nothing" for r in json.loads(out.getvalue())["rules"]), out.getvalue()
    assert code in (0, 1)


@pytest.mark.parametrize("rule", RULES)
def test_every_clean_list_is_clean_under_all_four_rules(rule):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main([os.path.join(FIX, f"{rule}-clean.jsonl"), "--snapshot", TREE, "--config", CONFIG,
                         "--rules", ",".join(RULES)])
    assert code == 0, out.getvalue()


def test_padre_is_a_father_in_spanish_not_only_a_priest_in_portuguese(tmp_path):
    ops = [{"op": "add_note", "key": "n", "text": f"Luis Zorvalo, padre de la bautizada, {FS}1:1:QZZ9-AAB",
            "attach": [{"person": "I2"}]}]
    assert lint(write(tmp_path, ops), "role")[:2] == (0, {})
    assert E.ROLE_WORDS["padre"] == {"father", "priest"}


def test_a_name_is_not_a_role_word_for_its_own_person():
    assert E.role_words("Peter Brin, father of the child", "Peter Brin") == {"father"}
    assert E.role_words("peter Brin", "Peter Brin") == set()


def test_a_godparent_named_with_his_role_and_ark_is_not_a_principal_finding(tmp_path):
    ops = [{"op": "add_note", "key": "n", "text": f"padrino Tomás Brin {FS}1:1:QZZ9-AAD", "attach": [{"person": "I1"}]},
           {"op": "add_citation", "key": "c", "source": "S1", "page": f"{FS}1:1:QZZ9-AAD godfather Tomás Brin",
            "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "principal")[1] == {"principal-nonprincipal": ["c"]}, "a page is never excused"


def test_short_and_empty_spans_next_to_a_cited_record_are_checked(tmp_path):
    ops = [{"op": "add_note", "key": "n-short", "text": f"surname «Zorvalla», born «3 Mar 1801», father «» {FS}1:1:QZZ9-AAA",
            "attach": [{"person": "I1"}]},
           {"op": "add_note", "key": "n-none", "text": f"godmother «None» {FS}1:1:QZZ9-AAA", "attach": [{"person": "I1"}]},
           {"op": "add_note", "key": "n-ok", "text": f"father «Luis Zorvalo» {FS}1:1:QZZ9-AAA", "attach": [{"person": "I1"}]},
           {"op": "add_note", "key": "n-title", "text": "see the «Libro de Bautismos»", "attach": [{"person": "I1"}]}]
    code, codes, _ = lint(write(tmp_path, ops), "quote")
    assert (code, codes) == (1, {"quote-short": ["n-short", "n-short", "n-short", "n-none"]})


def test_a_quotation_that_cites_nothing_is_not_matched_against_unrelated_records(tmp_path):
    # the words are in Pedro's record, which this note does not cite
    ops = [{"op": "add_note", "key": "n", "text": "The priest wrote «bauticé en la ermita del Santo Cristo del Valle».",
            "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "quote")[1] == {"quote-notfound": ["n"]}


BULLETIN = "https://bulletin.example.org/1998/12/edict.pdf"


def test_a_quotation_from_a_source_with_no_saved_text_is_norec(tmp_path):
    ops = [{"op": "add_citation", "key": "c", "source": "S1", "page": BULLETIN, "attach": [{"person": "I1"}]},
           {"op": "add_note", "key": "n", "text": "The edict calls «a los herederos de la finca del molino»",
            "attach": [{"citation": "key:c"}]}]
    code, _, res = lint(write(tmp_path, ops), "quote")
    assert (code, res["status"]) == (2, "nothing") and BULLETIN in res["reason"], res
    shutil.copytree(RECORDS, tmp_path / "saved")
    (tmp_path / "saved" / "edict.txt").write_text(f"{BULLETIN}\nSe cita a los herederos de la finca del molino.\n",
                                                  encoding="utf-8")
    assert lint(write(tmp_path, ops), "quote", "--records", str(tmp_path / "saved"))[:2] == (0, {})
    ops[1]["text"] = "The edict calls «a los herederos de la casa del molino»"
    code, codes, _ = lint(write(tmp_path, ops), "quote", "--records", str(tmp_path / "saved"))
    assert codes == {"quote-notfound": ["n"]}


def test_a_media_on_the_cited_citation_makes_the_quotation_image_only(tmp_path):
    (tmp_path / "act-77.jpg").write_bytes(b"\xff\xd8\xff\xe0 invented")
    ops = [{"op": "add_citation", "key": "c", "source": "S1", "page": "https://images.example.org/viewer/item/77",
            "attach": [{"person": "I1"}]},
           {"op": "add_media", "key": "m", "file": str(tmp_path / "act-77.jpg"), "path": "media/act-77.jpg", "mime": "image/jpeg",
            "attach": [{"citation": "key:c"}]},
           {"op": "add_note", "key": "n", "text": "The act reads «estas palabras no tienen lectura firmada»",
            "attach": [{"citation": "key:c"}]}]
    assert lint(write(tmp_path, ops), "quote")[1] == {"quote-image": ["n"]}


def test_a_readings_file_saved_with_a_bom_still_counts_its_first_row(tmp_path):
    rows = tmp_path / "readings.tsv"
    rows.write_bytes("﻿estas palabras no tienen lectura firmada\tabcdef12\tA. Reader\r\n".encode("utf-8"))
    ops = [{"op": "add_note", "key": "n", "text": f"«estas palabras no tienen lectura firmada» {FS}3:1:QZZ9-IMG2",
            "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "quote", "--readings", str(rows))[:2] == (0, {})


def test_an_override_holds_only_for_its_own_list(tmp_path):
    # the same op key in another list is another op: the override for placement-clean.jsonl does not reach it
    other = tmp_path / "other.jsonl"
    shutil.copy(os.path.join(FIX, "placement-clean.jsonl"), other)
    code, codes, _ = lint(str(other), "placement")
    assert codes == {"placement-parish": ["e-bap-pedro"]}


# --- guards the first rounds left without a test: each test below goes red when its guard is removed ---------

GX = "http://gedcomx.org/"


def gx_record(path, people, parents=(), place=None):
    """Write an invented raw GEDCOM X record: people are (id, ark, name, gender, principal, facts, role)."""
    persons = []
    for pid, ark, name, gender, principal, facts, role in people:
        p = {"id": pid, "principal": principal, "gender": {"type": GX + gender} if gender else {},
             "identifiers": {GX + "Persistent": [FS + "1:1:" + ark]}, "names": [{"nameForms": [{"fullText": name}]}],
             "facts": [{"type": GX + f} for f in facts]}
        if role:
            p["display"] = {"name": name, "role": role}
        persons.append(p)
    doc = {"persons": persons, "relationships": [{"type": GX + "ParentChild", "person1": {"resource": "#" + a},
                                                   "person2": {"resource": "#" + b}} for a, b in parents]}
    if place:
        doc["fields"] = [{"labelId": "EVENT_PLACE_ORIG", "text": place}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def saved_copy(tmp_path):
    shutil.copytree(RECORDS, tmp_path / "saved")
    return tmp_path / "saved"


def tree_copy(tmp_path, edit):
    data = json.loads(open(TREE, encoding="utf-8").read())
    edit(data)
    p = tmp_path / "tree.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_a_sacrament_on_a_town_is_reported_even_when_its_record_is_not_saved(tmp_path):
    ops = [{"op": "add_event", "key": "e", "person": "I1", "type": "Baptism", "place": "P1"},
           {"op": "add_citation", "key": "c", "source": "S1", "page": FS + "1:1:QZZ1-NNA", "attach": [{"event": "key:e"}]}]
    assert lint(write(tmp_path, ops), "placement", config=None)[:2] == (1, {"placement-town": ["e"]})


def test_a_church_event_with_no_cited_record_is_not_a_parish_finding(tmp_path):
    ops = [{"op": "add_event", "key": "e", "person": "I1", "type": "Baptism", "place": "P2"}]
    assert lint(write(tmp_path, ops), "placement", config=None)[:2] == (0, {})


def test_an_update_event_is_placed_against_the_citations_the_event_already_has(tmp_path):
    ops = [{"op": "update_event", "key": "u", "event": "E1", "set": {"place": "P5"}}]  # E1 carries C1 (Ana's record)
    assert lint(write(tmp_path, ops), "placement")[:2] == (1, {"placement-parish": ["u"]})


def test_an_update_event_is_placed_against_the_citations_the_list_hangs_on_it(tmp_path):
    def drop(data):
        data["events"][0]["citation_list"] = []
    ops = [{"op": "add_citation", "key": "c", "source": "S1", "page": FS + "1:1:QZZ9-AAA", "attach": [{"event": "E1"}]},
           {"op": "update_event", "key": "u", "event": "E1", "set": {"place": "P5"}}]
    tree = tree_copy(tmp_path, drop)
    assert lint(write(tmp_path, ops), "placement", "--snapshot", tree)[:2] == (1, {"placement-parish": ["u"]})


def test_a_media_on_a_tree_citation_makes_the_quotation_image_only(tmp_path):
    def media(data):
        c2 = next(c for c in data["citations"] if c["gramps_id"] == "C2")
        c2["page"], c2["media_list"] = "https://images.example.org/viewer/item/9", [{"ref": "hM1"}]
    ops = [{"op": "add_note", "key": "n", "text": "The act reads «estas palabras no tienen lectura firmada»",
            "attach": [{"citation": "C2"}]}]
    tree = tree_copy(tmp_path, media)
    assert lint(write(tmp_path, ops), "quote", "--snapshot", tree)[:2] == (1, {"quote-image": ["n"]})


def test_a_quotation_in_a_media_description_is_image_only(tmp_path):
    (tmp_path / "act.jpg").write_bytes(b"\xff\xd8\xff\xe0 invented")
    ops = [{"op": "add_media", "key": "m", "file": str(tmp_path / "act.jpg"), "path": "media/act.jpg",
            "mime": "image/jpeg", "desc": "The act reads «estas palabras no tienen lectura firmada»",
            "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "quote")[:2] == (1, {"quote-image": ["m"]})


def test_a_note_on_a_tree_citation_is_checked_against_that_citations_record(tmp_path):
    ops = [{"op": "add_note", "key": "n", "text": "The priest wrote «bauticé solemnemente a una niña que nació el día "
            "de ayer».", "attach": [{"citation": "C1"}]}]
    assert lint(write(tmp_path, ops), "quote")[:2] == (0, {})


def test_attach_existing_of_a_citation_the_list_adds_cites_its_page(tmp_path):
    ops = [{"op": "add_citation", "key": "c", "source": "S1", "page": FS + "1:1:QZZ9-AAB", "attach": [{"person": "I2"}]},
           {"op": "attach_existing", "key": "a", "object": {"citation": "key:c"}, "attach": [{"event": "E1"}]}]
    assert lint(write(tmp_path, ops), "principal")[:2] == (1, {"principal-nonprincipal": ["c", "a"]})


def test_an_updated_page_with_role_words_is_still_nonprincipal(tmp_path):
    ops = [{"op": "update_citation", "key": "u", "citation": "C2", "expect": FS + "1:1:QZZ9-AAB",
            "set": {"page": "padre Luis Zorvalo, " + FS + "1:1:QZZ9-AAB"}}]
    assert lint(write(tmp_path, ops), "principal")[:2] == (1, {"principal-nonprincipal": ["u"]})


def test_a_role_word_far_from_the_personas_name_does_not_excuse_it(tmp_path):
    ops = [{"op": "add_note", "key": "n", "text": "The father is not named in the entry; see " + FS + "1:1:QZZ9-AAD",
            "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "principal")[:2] == (1, {"principal-nonprincipal": ["n"]})


def test_a_parent_the_index_gives_no_role_is_still_a_parent(tmp_path):
    # Pedro's record links Juan Morel as his parent and gives him no role text
    ops = [{"op": "add_note", "key": "n", "text": "padrino: Juan Morel, " + FS + "1:1:QZZ8-BBA", "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "role")[:2] == (1, {"role-mismatch": ["n"]})


def test_a_father_word_on_a_parent_of_unknown_sex_is_no_mismatch(tmp_path):
    gx_record(tmp_path / "saved" / "rec.json", [("p1", "QZZ3-GGA", "Rita Ondaro", "Female", True, ["Christening"], ""),
                                                ("p2", "QZZ3-GGB", "Luis Vesquel", "", False, [], "")],
              parents=[("p2", "p1")])
    ops = [{"op": "add_note", "key": "n", "text": "father Luis Vesquel, " + FS + "1:1:QZZ3-GGA", "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "role", "--records", str(tmp_path / "saved"))[:2] == (0, {})


def test_a_one_word_name_is_found_in_the_text(tmp_path):
    gx_record(tmp_path / "saved" / "rec.json", [("p1", "QZZ3-HHA", "Rita Ondaro", "Female", True, ["Christening"], ""),
                                                ("p2", "QZZ3-HHB", "Anselmo", "Male", False, [], "Godparent")])
    ops = [{"op": "add_note", "key": "n", "text": "padre: Anselmo, " + FS + "1:1:QZZ3-HHA", "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "role", "--records", str(tmp_path / "saved"))[:2] == (1, {"role-mismatch": ["n"]})


def test_a_bulletin_saved_as_a_gwt_fscache_text_body_is_read(tmp_path):
    saved = saved_copy(tmp_path)
    url = "https://bulletin.example.org/1998/edict-12"
    (saved / "fscache-copy" / "9f" / ("9e" + "0" * 62 + ".json")).write_text(
        json.dumps({"url": url, "status": 200, "fetched": 1767323045}), encoding="utf-8")
    (saved / "fscache-copy" / "9f" / ("9e" + "0" * 62 + ".body")).write_text(
        "Se cita a los herederos desconocidos del causante para que comparezcan.", encoding="utf-8")
    ops = [{"op": "add_citation", "key": "c", "source": "S1", "page": url, "attach": [{"person": "I1"}]},
           {"op": "add_note", "key": "n", "text": "The edict says «herederos desconocidos del causante para que "
            "comparezcan».", "attach": [{"citation": "key:c"}]}]
    assert lint(write(tmp_path, ops), "quote", "--records", str(saved))[:2] == (0, {})


def test_the_text_of_a_gwt_read_envelope_is_read(tmp_path):
    saved = saved_copy(tmp_path)
    url = "https://archive.example.org/act/5"
    (saved / "act-5.json").write_text(json.dumps({"url": url, "final_url": url, "text": "En la villa de Villazul "
                                                  "comparecieron los dos testigos del acta"}), encoding="utf-8")
    ops = [{"op": "add_citation", "key": "c", "source": "S1", "page": url, "attach": [{"person": "I1"}]},
           {"op": "add_note", "key": "n", "text": "The act says «comparecieron los dos testigos del acta».",
            "attach": [{"citation": "key:c"}]}]
    assert lint(write(tmp_path, ops), "quote", "--records", str(saved))[:2] == (0, {})


def test_a_transcription_saved_for_the_principal_backs_a_quote_cited_on_another_persona(tmp_path):
    saved = saved_copy(tmp_path)
    (saved / "ana.txt").write_text("ark:/61903/1:1:QZZ9-AAA\ny fueron sus padrinos los vecinos de la villa\n",
                                   encoding="utf-8")
    ops = [{"op": "add_note", "key": "n", "text": "«fueron sus padrinos los vecinos de la villa» " + FS + "1:1:QZZ9-AAD",
            "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "quote", "--records", str(saved))[:2] == (0, {})


def test_a_candidate_row_of_another_source_is_not_a_familysearch_record(tmp_path):
    saved = saved_copy(tmp_path)
    (saved / "other.candidates.json").write_text(json.dumps({"candidates": [
        {"source": "matchid", "id": "QZZ2-KKA", "name": "Rita Ondaro", "principal": True}]}), encoding="utf-8")
    ops = [{"op": "add_citation", "key": "c", "source": "S1", "page": FS + "1:1:QZZ2-KKA", "attach": [{"person": "I1"}]}]
    assert lint(write(tmp_path, ops), "principal", "--records", str(saved))[0] == 2


def test_a_candidate_row_with_no_facts_is_no_event_finding(tmp_path):
    saved = saved_copy(tmp_path)
    (saved / "rita.candidates.json").write_text(json.dumps({"candidates": [
        {"source": "familysearch", "id": "QZZ2-LLA", "name": "Rita Ondaro", "role": "Principal", "principal": True,
         "events": {}}]}), encoding="utf-8")
    ops = [{"op": "add_event", "key": "e", "person": "I1", "type": "Death"},
           {"op": "add_citation", "key": "c", "source": "S1", "page": FS + "1:1:QZZ2-LLA", "attach": [{"event": "key:e"}]}]
    assert lint(write(tmp_path, ops), "principal", "--records", str(saved))[:2] == (0, {})
