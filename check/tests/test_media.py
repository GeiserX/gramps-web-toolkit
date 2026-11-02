"""The media kinds: non-viewable, file missing, and with --download tiny, uniform and the same page twice."""

import copy
import io
import json
import random
import sys
import urllib.parse

import pytest

from gwt_check import checks, media, selftest
from gwt_check.cli import main
from gwt_check.client import Client
from gwt_check.tree import Tree

KEY = "status"  # in `_tree`'s fields: what the server said about that media object, kept beside the objects


def _tree(*extra, citation="C0001"):
    """The self-test's clean tree plus media objects `extra` (dicts of fields), all on one citation."""
    d = selftest.clean_tree()
    d[media.STATUS] = {"hO0001": _measured(dhash="0f" * 32)[KEY]}
    for n, fields in enumerate(extra, 50):
        fields = dict(fields)
        d[media.STATUS][f"hO00{n}"] = fields.pop(KEY, {"file_missing": False})
        m = {**copy.deepcopy(d["media"][0]), "handle": f"hO00{n}", "gramps_id": f"O00{n}",
             "checksum": f"{n:032x}", **fields}
        d["media"].append(m)
        next(c for c in d["citations"] if c["gramps_id"] == citation)["media_list"].append({"ref": m["handle"], "rect": []})
    return d


def _found(d, scope=None):
    findings, info = checks.run_checks(Tree(d, media_status=d[media.STATUS]), scope)
    return {(f["check"], f["key"]) for f in findings if f["check"].startswith("media-")}, info


def _measured(**kw):
    return {KEY: {"file_missing": False, "w": 256, "h": 192, "tone": 0.6, "dhash": "f0" * 32, **kw}}


# -- pure parts ------------------------------------------------------------------------------------

@pytest.mark.parametrize("mime,path,want", [
    ("text/plain", "a.txt", "text/plain"),
    ("text/html", "a.html", "text/html"),
    ("application/json", "a.json", "application/json"),
    ("message/rfc822", "a.eml", "message/rfc822"),
    ("application/x-tar", "a.tar", "application/x-tar"),
    ("application/gzip", "a.tar.gz", "application/gzip"),
    ("", "notas/acta.txt", "text/plain"),     # no MIME stored: read from the file name
    ("image/jpeg", "a.jpg", None),
    ("application/pdf", "a.pdf", None),
    ("audio/mpeg", "a.mp3", None),             # a recording plays; it is not a grey icon
])
def test_non_viewable_by_mime_or_file_name(mime, path, want):
    assert media.non_viewable({"mime": mime, "path": path}) == want


@pytest.mark.parametrize("mime,path,want", [
    ("image/jpeg", "a.jpg", True),
    ("application/pdf", "a.pdf", True),
    ("", "escaneos/acta.jpg", True),     # no MIME stored: read from the file name, as non_viewable does
    ("", "notas/acta.txt", False),
    ("text/plain", "a.jpg", False),
])
def test_is_image_by_mime_or_file_name(mime, path, want):
    assert media.is_image({"mime": mime, "path": path}) is want


def test_tone_share_counts_the_window_around_the_top_tone():
    hist = [0] * 256
    hist[128] = 90
    hist[128 + media.TONE_WINDOW] = 5      # inside the window
    hist[128 + media.TONE_WINDOW + 1] = 5  # just outside
    assert media.tone_share(hist) == pytest.approx(0.95)
    assert media.tone_share([0] * 256) == 0.0


def test_dhash_sets_a_bit_where_the_left_neighbour_is_brighter():
    assert media.dhash([[3, 2, 1]]) == 0b11
    assert media.dhash([[1, 2, 3]]) == 0
    assert media.dhash([[2, 1], [1, 2]]) == 0b10
    assert media.hamming(0b1011, 0b0001) == 2


# -- the checks on what the media objects carry ------------------------------------------------------

def test_a_text_file_is_non_viewable_and_suggests_a_transcript():
    found, _ = _found(_tree({"mime": "text/plain", "path": "ejemplo/acta.txt"}))
    assert found == {("media-non-viewable", "O0050")}
    d = _tree({"mime": "text/plain", "path": "ejemplo/acta.txt"})
    findings, _ = checks.run_checks(Tree(d, media_status=d[media.STATUS]))
    assert "add_transcription" in next(f["detail"] for f in findings if f["check"] == "media-non-viewable")


@pytest.mark.parametrize("mime,path", [("application/x-tar", "ejemplo/fotos.tar"), ("application/json", "ejemplo/a.json")])
def test_an_archive_or_json_file_is_not_told_to_become_a_transcript(mime, path):
    d = _tree({"mime": mime, "path": path})
    findings, _ = checks.run_checks(Tree(d, media_status=d[media.STATUS]))
    detail = next(f["detail"] for f in findings if f["check"] == "media-non-viewable")
    assert "add_transcription" not in detail and "unpack or convert" in detail


def test_a_malformed_measure_in_a_dump_is_skipped_not_a_crash():
    d = _tree(_measured(dhash="zz" * 32), _measured(dhash="0f"), _measured(w="40", h=None, tone="1"))
    found, _ = _found(d)
    assert found == set()


def test_a_missing_file_is_found_and_an_unread_one_is_counted():
    found, info = _found(_tree({KEY: {"file_missing": True}}, {KEY: {}}))
    assert found == {("media-file-missing", "O0050")}
    assert info["media files not checked"] == 1  # O0051: the server was never asked about it


def test_annotate_keeps_earlier_measures():
    d = _tree()
    status = media.annotate(FakeClient(missing={"hO0001"}), d, d.pop(media.STATUS))
    assert status["hO0001"]["file_missing"] is True and status["hO0001"]["dhash"] == "0f" * 32


def test_thumbnails_not_read_are_counted_in_the_report():
    _, info = _found(_tree({KEY: {"file_missing": False, "unread": True}}))
    assert info["media thumbnails not read"] == 1


def test_a_tree_read_with_every_file_checked_has_no_note():
    _, info = _found(_tree({KEY: {"file_missing": False}}))
    assert "media files not checked" not in info


@pytest.mark.parametrize("w,h,tiny", [(40, 40, True), (199, 120, True), (200, 120, False), (300, 40, False),
                                      (40, 300, False)])
def test_tiny_is_the_longer_side_under_min_side(w, h, tiny):
    found, _ = _found(_tree(_measured(w=w, h=h, dhash="00" * 32)))
    assert (("media-tiny", "O0050") in found) is tiny


@pytest.mark.parametrize("tone,uniform", [(0.98, True), (0.979, False), (1.0, True)])
def test_uniform_from_the_share_of_one_tone(tone, uniform):
    found, _ = _found(_tree(_measured(tone=tone, dhash="00" * 32)))
    assert (("media-uniform", "O0050") in found) is uniform


def _bits_apart(n):
    """A hash `n` bits away from the clean tree's O0001 ("0f" * 32)."""
    h = int("0f" * 32, 16)
    for i in range(n):
        h ^= 1 << (4 + 8 * i)  # a bit O0001 has clear
    return f"{h:064x}"


@pytest.mark.parametrize("bits,dup", [(0, True), (media.DUP_BITS, True), (media.DUP_BITS + 1, False)])
def test_the_same_page_twice_on_one_citation(bits, dup):
    found, _ = _found(_tree(_measured(dhash=_bits_apart(bits))))
    assert (("media-dup-page", "C0001 O0001,O0050") in found) is dup


def test_a_same_page_pair_asks_to_compare_before_detaching():
    # pages of one printed register or form can hash alike, so the finding is a lead, not a verdict
    d = _tree(_measured(dhash="0f" * 32))
    findings, _ = checks.run_checks(Tree(d, media_status=d[media.STATUS]))
    detail = next(f["detail"] for f in findings if f["check"] == "media-dup-page")
    assert "may be the same page" in detail and "compare them" in detail


def test_near_images_on_two_citations_are_not_a_pair():
    found, _ = _found(_tree(_measured(dhash="0f" * 32), citation="C0002"))
    assert not {f for f in found if f[0] == "media-dup-page"}


def test_the_same_file_twice_is_left_to_dup_media():
    found, _ = _found(_tree({**_measured(dhash="0f" * 32), "checksum": "0" * 32}))
    assert not {f for f in found if f[0] == "media-dup-page"}


def test_scope_old_media_are_not_blamed_on_a_run():
    d = _tree({"mime": "text/plain"}, {KEY: {"file_missing": True}}, _measured(dhash="0f" * 32))
    found, _ = _found(d, scope=set())
    assert found == set()
    found, _ = _found(d, scope={("media", "hO0052")})  # the new page against the old one: a pair
    assert found == {("media-dup-page", "C0001 O0001,O0052")}


# -- reading what the server says ------------------------------------------------------------------

def _bare():
    """The clean tree as a server sends it: nothing measured yet."""
    return selftest.clean_tree()


class FakeClient(Client):
    """The real Client over a dict of thumbnails (bytes, or the HTTP status the server answers); counts requests."""

    def __init__(self, missing=(), thumbs=None):
        super().__init__("https://gramps.example.org", "u", "pw", sleep=lambda s: None)
        self.token = "t"
        self.missing, self.thumbs, self.calls = set(missing), thumbs or {}, []

    def _call(self, req):
        url = urllib.parse.urlparse(req.full_url)
        path = url.path.removeprefix("/api")
        if path == "/media/":
            q = dict(urllib.parse.parse_qsl(url.query))
            self.calls.append((path, q))
            assert {k: v for k, v in q.items() if k != "pagesize"} == {"filemissing": "1", "keys": "handle"}
            return 200, {}, json.dumps([{"handle": h} for h in self.missing]).encode()
        self.calls.append((path, None))
        got = self.thumbs.get(path.split("/")[2], 404)
        return (got, {}, b"") if isinstance(got, int) else (200, {}, got)


def test_annotate_marks_missing_files_in_one_request():
    d = _bare()
    client = FakeClient(missing={"hO0001"})
    assert media.annotate(client, d) == {"hO0001": {"file_missing": True}} and len(client.calls) == 1


def test_each_missing_file_list_is_a_new_request_to_the_server_cache():
    # Gramps Web answers a list from its cache until the tree changes, and a deleted file changes nothing in it
    client = FakeClient()
    media.annotate(client, _bare())
    media.annotate(client, _bare())
    (_, first), (_, second) = client.calls
    assert first["pagesize"] != second["pagesize"]


def test_download_without_pillow_is_skipped_with_a_note(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "PIL", None)  # import PIL raises ImportError
    d = _bare()
    client = FakeClient()
    status = media.annotate(client, d, download=True)
    assert "needs Pillow" in capsys.readouterr().err
    assert status == {"hO0001": {"file_missing": False}} and len(client.calls) == 1


def test_download_with_a_tree_file_is_bad_input(tmp_path, capsys):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(selftest.clean_tree()), encoding="utf-8")
    assert main(["check", str(path), "--download"]) == 2
    assert "--download" in capsys.readouterr().err


def _serve(monkeypatch, d, write=None):
    from test_check import LiveServer
    import gwt_check.client as clientmod

    server = LiveServer(d, write)
    server.missing_files = {"hO0001"}
    monkeypatch.setattr(clientmod.urllib.request, "urlopen", server)
    monkeypatch.setattr(clientmod.time, "sleep", lambda s: None)
    for k, v in {"GWT_URL": "https://gramps.example.org", "GWT_USERNAME": "u", "GWT_PASSWORD": "pw"}.items():
        monkeypatch.setenv(k, v)
    return server


def test_a_live_check_reports_a_missing_file(monkeypatch, tmp_path, capsys):
    _serve(monkeypatch, _bare())
    report, dump = str(tmp_path / "r.json"), str(tmp_path / "d.json")
    assert main(["check", "--json", report, "--save-dump", dump]) == 1, capsys.readouterr().err
    assert {(f["check"], f["key"]) for f in json.load(open(report))["new"]} == {("media-file-missing", "O0001")}
    # the saved dump carries it, so an offline check of it says the same
    assert main(["check", dump]) == 1


def test_a_dump_keeps_media_objects_as_the_server_sent_them(monkeypatch, tmp_path):
    # gwt-apply writes objects from a dump back to the server, which rejects (or stores) a key it does not know
    d = _bare()
    _serve(monkeypatch, d)
    dump = str(tmp_path / "d.json")
    assert main(["dump", dump]) == 0
    saved = json.load(open(dump))
    assert [set(m) for m in saved["objects"]["media"]] == [set(m) for m in d["media"]]
    assert saved[media.STATUS] == {"hO0001": {"file_missing": True}}


def test_a_missing_file_survives_a_read_that_settled_while_someone_wrote(monkeypatch, tmp_path):
    # the passes never see what the server says about files: it is read once, after them
    def busy(server, t, q):
        if q.get("keys"):
            p = server.data["people"][-1]
            p["primary_name"]["first_name"] += "x"
            server.tick(p)
    _serve(monkeypatch, _bare(), busy)
    report = str(tmp_path / "r.json")
    assert main(["check", "--json", report]) == 1
    r = json.load(open(report))
    assert r["passes"] == 4 and [f["key"] for f in r["new"]] == ["O0001"]


def test_download_asks_no_thumbnail_of_a_missing_file_or_a_text_file(monkeypatch):
    import types
    monkeypatch.setitem(sys.modules, "PIL", types.ModuleType("PIL"))  # found, never used: nothing to measure
    d = _bare()
    d["media"].append({**copy.deepcopy(d["media"][0]), "handle": "hO0002", "mime": "text/plain"})
    client = FakeClient(missing={"hO0001"})
    media.annotate(client, d, download=True)
    assert len(client.calls) == 1  # the list of missing files, and no thumbnail


# -- Pillow on generated images (the server's thumbnail is simulated with Pillow's own) ------------------

def _page(seed, shift=0, size=(1200, 1600)):
    """An invented page: dark text lines on light paper, at places drawn from `seed`."""
    Image = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw

    img = Image.new("L", size, 235)
    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for y in range(80, size[1] - 80, 46):
        x = 90 + rnd.randrange(0, 200)
        draw.rectangle((x + shift, y, x + shift + rnd.randrange(300, 900), y + 14), fill=40)
    return img


def _thumb(img):
    """The thumbnail as Gramps Web sends it: AVIF (PNG where this Pillow cannot write AVIF)."""
    from PIL import features

    img = img.copy()
    img.thumbnail((media.THUMB, media.THUMB))
    buf = io.BytesIO()
    img.save(buf, format="AVIF" if features.check("avif") else "PNG")
    return buf.getvalue()


def test_measure_tiny_uniform_and_the_same_page_shifted_one_pixel():
    Image = pytest.importorskip("PIL.Image")
    tiny = media.measure(_thumb(Image.new("RGB", (40, 40), (200, 30, 30))))
    assert (tiny["w"], tiny["h"]) == (40, 40)
    grey = media.measure(_thumb(Image.new("RGB", (2000, 1500), (128, 128, 128))))
    assert grey["tone"] >= media.UNIFORM_SHARE and max(grey["w"], grey["h"]) == media.THUMB
    page = media.measure(_thumb(_page(1)))
    assert page["tone"] < media.UNIFORM_SHARE
    again = media.measure(_thumb(_page(1, shift=1)))
    other = media.measure(_thumb(_page(2)))
    near = media.hamming(int(page["dhash"], 16), int(again["dhash"], 16))
    far = media.hamming(int(page["dhash"], 16), int(other["dhash"], 16))
    assert near <= media.DUP_BITS < far, (near, far)


def test_download_measures_the_thumbnails_and_counts_what_it_could_not_read(capsys):
    pytest.importorskip("PIL.Image")
    d = _bare()
    second = {**copy.deepcopy(d["media"][0]), "handle": "hO0002", "gramps_id": "O0002"}
    d["media"].append(second)
    client = FakeClient(thumbs={"hO0001": _thumb(_page(1))})
    status = media.annotate(client, d, download=True, workers=2)
    assert status["hO0001"]["w"] == 192 and "dhash" in status["hO0001"]
    assert status["hO0002"] == {"file_missing": False, "unread": True}  # 404: nothing measured, counted
    assert "1 thumbnail(s) measured, 1 the server would not give" in capsys.readouterr().err


def test_a_thumbnail_the_server_cannot_make_is_counted_not_the_end_of_the_run(capsys):
    # 422: a file the server cannot open (corrupt, HEIC without its plugin); 501: a PDF without poppler
    pytest.importorskip("PIL.Image")
    d = _bare()
    for h in ("hO0002", "hO0003"):
        d["media"].append({**copy.deepcopy(d["media"][0]), "handle": h, "gramps_id": h[1:]})
    client = FakeClient(thumbs={"hO0001": _thumb(_page(1)), "hO0002": 422, "hO0003": 501})
    status = media.annotate(client, d, download=True, workers=1)
    assert "dhash" in status["hO0001"] and "unread" not in status["hO0001"]
    assert status["hO0002"] == status["hO0003"] == {"file_missing": False, "unread": True}
    assert sum(p == "/media/hO0003/thumbnail/256" for p, _ in client.calls) == 1  # a 501 is not tried again
    assert "1 thumbnail(s) measured, 2 the server would not give" in capsys.readouterr().err


def test_only_check_and_dump_ask_for_missing_files(monkeypatch, capsys):
    server = _serve(monkeypatch, _bare())
    assert main(["repos"]) == 0, capsys.readouterr().err
    assert not [q for _, q in server.calls if "filemissing" in q]


def test_a_failing_list_of_missing_files_is_a_note_not_the_end_of_the_check(monkeypatch, tmp_path, capsys):
    import urllib.error

    def broken_storage(server, t, q):  # media storage that errors: an S3 list failure answers 500
        if "filemissing" in q:
            raise urllib.error.HTTPError("", 500, "Internal Server Error", {}, io.BytesIO(b"{}"))
    _serve(monkeypatch, _bare(), broken_storage)
    import gwt_check.client as clientmod
    monkeypatch.setattr(clientmod, "backoff", lambda attempt: 0)  # its five retries, without the waits
    report = str(tmp_path / "r.json")
    assert main(["check", "--json", report]) == 0
    assert "list of missing files failed" in capsys.readouterr().err
    assert json.load(open(report))["info"]["media files not checked"] == 1
