"""`expect` on update_person, update_event, update_place and update_family: the values the list read, so a field
someone changed since is a conflict and is never overwritten. Every name here is invented."""

import pytest

from fake_api import FakeGrampsWeb
from gwt_apply.client import Client
from gwt_apply.dates import parse_date
from gwt_apply.ops import Applier, validate

R = "ruling-0001"


@pytest.fixture
def fake():
    f = FakeGrampsWeb()
    f.add("people", {"handle": "ana", "gender": 0, "primary_name": {
        "first_name": "Ana", "call": "Anita", "surname_list": [{"surname": "Example", "primary": True}]}})  # I0001
    f.add("places", {"handle": "town", "name": {"value": "Exampleton"}, "place_type": "Town", "lat": "40.1",
                     "long": "-3.2"})  # P0001
    f.add("places", {"handle": "other", "name": {"value": "Otherton"}, "place_type": "Town"})  # P0002
    f.add("places", {"handle": "street", "name": {"value": "High Street"}, "place_type": "Street",
                     "placeref_list": [{"ref": "town"}]})  # P0003
    f.add("events", {"handle": "ev", "type": "Birth", "date": parse_date("1890-03-16"), "place": "town",
                     "description": "old text"})  # E0001
    f.add("families", {"handle": "fam", "type": "Unknown", "mother_handle": "ana"})  # F0001
    return f


def run(fake, ops, *, dry_run=False, verify=False):
    c = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=dry_run or verify, session=fake)
    c.login()
    return Applier(c, retries=3, retry_wait=0, verify=verify).run(ops)


EVENT = {"op": "update_event", "key": "e", "ruling": R, "event": "E0001", "set": {"description": "new text"},
         "expect": {"description": "old text"}}


def test_update_event_with_expect_writes_when_the_tree_has_what_the_list_read(fake):
    assert validate([EVENT]) == []
    assert run(fake, [EVENT])[0].status == "done"
    assert fake.store["events"]["ev"]["description"] == "new text"


def test_update_event_with_expect_refuses_a_field_changed_since(fake):
    fake.store["events"]["ev"]["description"] = "an edit made since"
    for dry_run in (True, False):
        res = run(fake, [EVENT], dry_run=dry_run)[0]
        assert res.status == "conflict", res.actions
        assert "no longer has description 'old text' (it has 'an edit made since')" in res.actions[-1], res.actions
    assert fake.writes() == []
    assert fake.store["events"]["ev"]["description"] == "an edit made since"


def test_a_landed_update_with_expect_reruns_and_verifies_as_a_skip(fake):
    assert run(fake, [EVENT])[0].status == "done"
    assert run(fake, [EVENT])[0].status == "skip"  # the field holds the new value: a rerun, not a conflict
    assert run(fake, [EVENT], verify=True)[0].status == "skip"


def test_every_field_expect_names_is_compared(fake):
    op = dict(EVENT, set={"description": "new text", "date": "1891"}, expect={"description": "old text", "date": "1889"})
    res = run(fake, [op], dry_run=True)[0]
    assert res.status == "conflict" and "no longer has date '1889' (it has '1890-03-16')" in res.actions[-1], res.actions


def test_expect_text_ignores_spaces_at_either_end_but_not_case(fake):
    fake.store["events"]["ev"]["description"] = "old text "
    assert run(fake, [dict(EVENT, expect={"description": " old text"})], dry_run=True)[0].status == "done"
    assert run(fake, [dict(EVENT, expect={"description": "Old text"})], dry_run=True)[0].status == "conflict"


def test_a_field_expect_does_not_name_is_not_compared(fake):
    fake.store["events"]["ev"]["description"] = "an edit made since"
    op = dict(EVENT, set={"description": "new text", "type": "Baptism"}, expect={"type": "Birth"})
    assert run(fake, [op])[0].status == "done"


def test_update_event_expect_compares_dates_as_dates_and_places_by_place(fake):
    op = dict(EVENT, set={"date": "1890-03-17", "place": "P0002"}, expect={"date": "1890-3-16", "place": "P0001"})
    assert run(fake, [op], dry_run=True)[0].status == "done"
    res = run(fake, [dict(op, expect={"date": "1890"})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has '1890-03-16')" in res.actions[-1], res.actions
    res = run(fake, [dict(op, expect={"place": "P0002"})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has 'P0001')" in res.actions[-1], res.actions


def test_update_person_expect(fake):
    op = {"op": "update_person", "key": "p", "ruling": R, "person": "I0001",
          "set": {"first_name": "Anna", "gender": "M", "call_name": "Nan"},
          "expect": {"first_name": "Ana", "gender": "F", "call_name": "Anita"}}
    assert run(fake, [op], dry_run=True)[0].status == "done"
    for read, has in (({"first_name": "Ann"}, "Ana"), ({"gender": "U"}, "F"), ({"call_name": "Annie"}, "Anita")):
        res = run(fake, [dict(op, expect=read)], dry_run=True)[0]
        assert res.status == "conflict" and f"(it has {has!r})" in res.actions[-1], res.actions
    res = run(fake, [dict(op, set={"surname": "Sample"}, expect={"surname": "Other"})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has 'Example')" in res.actions[-1], res.actions
    name = fake.store["people"]["ana"]["primary_name"]
    name["surname_list"] = [{"surname": "Birthname"}, {"surname": "Example", "primary": True}]
    res = run(fake, [dict(op, set={"surname": "Sample"}, expect={"surname": "Birthname"})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has 'Example')" in res.actions[-1], res.actions  # the primary surname
    name["surname_list"] = []  # a person known by first name only
    assert run(fake, [dict(op, set={"surname": "Sample"}, expect={"surname": ""})], dry_run=True)[0].status == "done"


def test_update_place_expect(fake):
    op = {"op": "update_place", "key": "pl", "ruling": R, "place": "P0003",
          "set": {"name": "Main Street", "enclosed_by": "P0002"}, "expect": {"name": "High Street", "enclosed_by": "P0001"}}
    assert run(fake, [op], dry_run=True)[0].status == "done"
    res = run(fake, [dict(op, expect={"enclosed_by": "P0002"})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has 'P0001')" in res.actions[-1], res.actions
    res = run(fake, [dict(op, expect={"name": "Low Street"})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has 'High Street')" in res.actions[-1], res.actions
    point = {"op": "update_place", "key": "pt", "ruling": R, "place": "P0001", "set": {"lat": 41, "long": -3.2},
             "expect": {"lat": 40.1}}
    assert run(fake, [point], dry_run=True)[0].status == "done"
    res = run(fake, [dict(point, expect={"lat": "40.2"})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has '40.1')" in res.actions[-1], res.actions


def test_update_family_expect(fake):
    op = {"op": "update_family", "key": "f", "ruling": R, "family": "F0001", "set": {"type": "Married"},
          "expect": {"type": "Unknown"}}
    assert run(fake, [op], dry_run=True)[0].status == "done"
    res = run(fake, [dict(op, expect={"type": "Civil Union"})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has 'Unknown')" in res.actions[-1], res.actions
    res = run(fake, [dict(op, set={"private": True}, expect={"private": True})], dry_run=True)[0]
    assert res.status == "conflict" and "(it has 'false')" in res.actions[-1], res.actions


@pytest.mark.parametrize("op, said", [
    (dict(EVENT, expect="old text"), "expect is an object"),
    (dict(EVENT, expect={}), "expect is an object"),
    (dict(EVENT, expect={"date": "1890"}), "expect takes only fields of set; ['date'] not in set"),
    (dict(EVENT, set={"date": "1891"}, expect={"date": "not a date"}), "not a date"),
    (dict(EVENT, set={"place": "P0002"}, expect={"place": "a1b2c3"}), "expect.place 'a1b2c3' is neither"),
    (dict(EVENT, expect={"description": None}), "expect.description is a text"),
    (dict(EVENT, set={"private": True}, expect={"private": "no"}), "expect.private is true or false"),
    ({"op": "update_person", "key": "p", "ruling": R, "person": "I0001", "set": {"gender": "M"},
      "expect": {"gender": "female"}}, "expect.gender is one of"),
    ({"op": "update_place", "key": "pl", "ruling": R, "place": "P0001", "set": {"lat": 1, "long": 2},
      "expect": {"lat": True}}, "expect.lat is a number"),
    ({"op": "update_family", "key": "f", "ruling": R, "family": "F0001", "set": {"father": "I0002"},
      "expect": {"father": ""}}, "expect takes no father or mother"),
])
def test_validate_refuses_a_bad_expect(op, said):
    errors = validate([op])
    assert len(errors) == 1 and said in errors[0], errors


def test_expect_is_optional_and_empty_values_mean_none(fake):
    assert validate([dict(EVENT, expect=None)]) != []  # present means an object
    assert validate([{k: v for k, v in EVENT.items() if k != "expect"}]) == []
    fake.store["events"]["ev"].pop("description")
    assert run(fake, [dict(EVENT, expect={"description": ""})], dry_run=True)[0].status == "done"
    fake.store["events"]["ev"]["place"] = None
    op = dict(EVENT, set={"place": "P0002"}, expect={"place": ""})
    assert run(fake, [op], dry_run=True)[0].status == "done"
