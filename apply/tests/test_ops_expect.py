"""A removal that finds nothing to remove is a conflict, so a mistyped ID never passes a dry run as a skip;
`expect` says when the list knows better. Every name here is invented."""

import pytest

from fake_api import FakeGrampsWeb
from gwt_apply.client import Client
from gwt_apply.ops import Applier, validate

R = "ruling-0001"


@pytest.fixture
def fake():
    f = FakeGrampsWeb()
    f.add("people", {"handle": "anna", "gender": 0, "primary_name": {
        "first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})  # I0001
    f.add("people", {"handle": "kid", "gender": 1, "primary_name": {
        "first_name": "Tom", "surname_list": [{"surname": "Example", "primary": True}]}})  # I0002
    for h, text in (("n1", "on Anna"), ("n2", "on nobody")):  # N0001, N0002
        f.add("notes", {"handle": h, "type": "Research", "format": 0, "text": {"string": text, "tags": []}})
    f.store["people"]["anna"]["note_list"] = ["n1"]
    f.add("tags", {"handle": "weak", "name": "Weak"})
    return f


def run(fake, ops, *, dry_run=False, verify=False):
    c = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=dry_run or verify, session=fake)
    c.login()
    return Applier(c, retries=3, retry_wait=0, verify=verify).run(ops)


DETACH = {"op": "detach", "key": "d", "ruling": R, "target": {"person": "I0001"}, "object": {"note": "N0002"}}  # a typo


def test_detach_of_a_reference_the_target_lacks_is_a_conflict(fake):
    res = run(fake, [DETACH], dry_run=True)[0]  # the first dry run already says so
    assert res.status == "conflict" and "does not have N0002 in note_list" in res.actions[-1], res.actions
    assert "expect" in res.actions[-1]
    assert run(fake, [dict(DETACH, expect="absent")])[0].status == "skip"
    assert run(fake, [dict(DETACH, expect="absent")], dry_run=True)[0].noop  # the tree already as the list asks
    assert run(fake, [DETACH], verify=True)[0].status == "skip"  # --verify: nothing left to remove is the landed state
    assert fake.writes() == []


def test_detach_whose_answer_was_lost_reads_its_own_write_back(fake):
    fake.drop_after = ("PUT", "/api/people/anna")  # the server takes the note off, the answer never arrives
    res = run(fake, [dict(DETACH, object={"note": "N0001"})])[0]
    assert res.status == "done" and res.answer_lost, res.actions
    assert fake.store["people"]["anna"]["note_list"] == []


def test_remove_tag_from_an_object_without_it_is_a_conflict(fake):
    op = {"op": "remove_tag", "key": "t", "ruling": R, "target": {"person": "I0002"}, "name": "Weak"}
    res = run(fake, [op], dry_run=True)[0]
    assert res.status == "conflict" and "does not have the tag 'Weak'" in res.actions[-1], res.actions
    assert run(fake, [dict(op, expect="absent")], dry_run=True)[0].status == "skip"


def test_unlink_child_of_a_child_the_family_lacks_is_a_conflict(fake):
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})  # F0001
    op = {"op": "unlink_child", "key": "u", "ruling": R, "family": "F0001", "child": "I0002", "reason": "not hers"}
    res = run(fake, [op], dry_run=True)[0]
    assert res.status == "conflict" and "not a child of Family F0001" in res.actions[-1], res.actions
    assert run(fake, [dict(op, expect="absent")], dry_run=True)[0].status == "skip"
    assert run(fake, [op], verify=True)[0].status == "skip"


def test_delete_of_an_object_the_tree_lacks_is_a_conflict(fake):
    op = {"op": "delete", "key": "x", "ruling": R, "object": {"note": "N0009"}, "expect_unreferenced": True}
    res = run(fake, [op], dry_run=True)[0]
    assert res.status == "conflict" and "the tree has no Note N0009" in res.actions[-1], res.actions
    assert run(fake, [dict(op, expect="absent")], dry_run=True)[0].status == "skip"
    assert run(fake, [op], verify=True)[0].status == "skip"


def test_attach_existing_with_expect_absent_refuses_a_link_already_there(fake):
    op = {"op": "attach_existing", "key": "a", "ruling": R, "object": {"note": "N0001"}, "attach": [{"person": "I0001"}]}
    assert run(fake, [op], dry_run=True)[0].status == "skip"  # without expect: a link already there is a skip
    res = run(fake, [dict(op, expect="absent")], dry_run=True)[0]
    assert res.status == "conflict" and "already has N0001 in note_list" in res.actions[-1], res.actions
    assert run(fake, [dict(op, expect="absent")], verify=True)[0].status == "skip"
    new = dict(op, object={"note": "N0002"}, expect="absent")
    assert run(fake, [new])[0].status == "done" and fake.store["people"]["anna"]["note_list"] == ["n1", "n2"]


def test_expect_takes_present_or_absent_on_the_ops_that_have_it():
    assert validate([dict(DETACH, expect="present")]) == []
    assert validate([dict(DETACH, expect="gone")])
    assert validate([dict(DETACH, expect=True)])


PAIR = [{"op": "detach", "key": "d", "ruling": R, "target": {"person": "I0001"}, "object": {"note": "N0001"}},
        {"op": "delete", "key": "x", "ruling": R, "object": {"note": "N0001"}, "expect_unreferenced": True}]


def test_rerun_of_a_landed_detach_then_delete_skips_with_expect_absent(fake):
    assert [r.status for r in run(fake, PAIR)] == ["done", "done"] and "n1" not in fake.store["notes"]
    again = run(fake, [dict(op, expect="absent") for op in PAIR])
    assert [r.status for r in again] == ["skip", "skip"], [r.actions for r in again]
    assert run(fake, PAIR)[0].status == "conflict"  # without expect and without an earlier log, still a conflict


def test_a_removal_an_earlier_apply_logged_as_done_is_a_skip(fake):
    run(fake, PAIR)
    c = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=True, session=fake)
    c.login()
    res = Applier(c, retries=3, retry_wait=0, landed=frozenset({"d", "x"})).run(PAIR)
    assert [r.status for r in res] == ["skip", "skip"], [r.actions for r in res]
    assert "an earlier --apply of this list removed it" in res[1].actions[-1]
    c.login()
    res = Applier(c, retries=3, retry_wait=0, landed=frozenset({"d"})).run(PAIR)  # only the keys it logged
    assert [r.status for r in res] == ["skip", "conflict"], [r.actions for r in res]
