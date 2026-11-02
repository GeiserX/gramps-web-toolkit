"""A whole-tree read says which page it is on before each request, so a read that hangs names its page."""

from fake_api import FakeGrampsWeb
from gwt_apply import snapshot
from gwt_apply.client import Client


def test_save_names_each_page_before_reading_it(tmp_path, capsys):
    fake = FakeGrampsWeb()
    fake.add("people", {"handle": "ann", "primary_name": {"first_name": "Ann"}})
    client = Client("https://gw.example", "u", "pw", tree="tree-test", session=fake)
    client.login()
    snapshot.save(client, str(tmp_path / "s.json.gz"))
    err = capsys.readouterr().err
    assert "snapshot: GET /people/ page 1 (0 Person so far)" in err
    assert err.count("snapshot: GET /") == len(fake.store)


def test_a_snapshot_records_when_its_read_began(tmp_path, capsys):
    import gzip
    import json
    import time

    fake = FakeGrampsWeb()
    fake.add("people", {"handle": "ann", "primary_name": {"first_name": "Ann"}})
    client = Client("https://gw.example", "u", "pw", tree="tree-test", session=fake)
    client.login()
    before = int(time.time())
    snapshot.save(client, str(tmp_path / "s.json.gz"))
    with gzip.open(tmp_path / "s.json.gz", "rt") as fh:
        started = json.load(fh)["read_started"]
    assert before <= started <= int(time.time())  # gwt-check --from reads it instead of guessing an hour back
    snapshot.refresh(client, snapshot.load(str(tmp_path / "s.json.gz")), str(tmp_path / "t.json.gz"))
    assert "snapshot: GET /people/ change stamps" in capsys.readouterr().err
    with gzip.open(tmp_path / "t.json.gz", "rt") as fh:
        assert isinstance(json.load(fh)["read_started"], int)
