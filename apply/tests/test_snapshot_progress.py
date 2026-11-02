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
