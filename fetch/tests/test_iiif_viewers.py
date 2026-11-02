"""Antenati's Referer, Canadiana viewer URLs, and Image API bases fetched at native width. Offline."""
import json
from pathlib import Path

import pytest

from gwt_fetch import iiif
from test_fetch import FakeClient, Resp, ns, rows

FIX = Path(__file__).parent / "fixtures" / "iiif"
ANTENATI = "https://antenati.cultura.gov.it/"
MANIFEST = "https://dam-antenati.cultura.gov.it/antenati/containers/Ex4mple/manifest"


def jpeg(width: int, height: int = 100) -> bytes:
    """The header of a baseline JPEG of this size: enough for a width check, no pixels."""
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0\x00\x0b\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def waf(body: Resp):
    """Antenati's WAF: 403 unless the Referer is the portal itself."""
    def answer(url, params, referer):
        return body if referer == ANTENATI else Resp(b"<html>403 Forbidden</html>", "text/html", 403)
    return answer


class RefererClient(FakeClient):
    """A FakeClient whose routes also see the Referer, so a route can play a WAF."""

    def get(self, url, referer=None, stream=False, params=None, **kw):
        self.calls.append((url, referer, params))
        for key, val in self.routes.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                r = val(url, params, referer) if callable(val) else val
                r.url = r.url or url
                return r
        return Resp(b"not found", status=404, url=url)


def antenati_routes():
    return {
        MANIFEST: waf(Resp((FIX / "antenati_manifest.json").read_bytes(), "application/json")),
        "https://iiif-antenati.cultura.gov.it/iiif/2/aAaa111/info.json": waf(Resp(
            json.dumps({"@id": "https://iiif-antenati.cultura.gov.it/iiif/2/aAaa111", "width": 4929,
                        "height": 3781, "protocol": "http://iiif.io/api/image"}), "application/json")),
        "https://iiif-antenati.cultura.gov.it/iiif/2/aAaa111/full/max/0/default.jpg":
            waf(Resp(jpeg(3610), "image/jpeg")),
        "https://iiif-antenati.cultura.gov.it/iiif/2/aAaa111/full/4929,/0/default.jpg":
            waf(Resp(jpeg(4929), "image/jpeg")),
    }


def test_antenati_manifest_url_gets_the_portal_referer():
    assert iiif.resolve(MANIFEST, None) == (MANIFEST, ANTENATI)
    image = "https://iiif-antenati.cultura.gov.it/iiif/2/aAaa111/info.json"
    assert iiif.resolve(image, None) == (image, ANTENATI)
    viewer = "https://viewer.example/m?manifest=" + MANIFEST
    assert iiif.resolve(viewer, None) == (MANIFEST, ANTENATI)
    # every other host keeps the Referer it had: the URL itself
    assert iiif.resolve("https://m.example/2.json", None) == ("https://m.example/2.json",) * 2
    assert iiif.resolve("https://v.example/x?manifest=https%3A%2F%2Fm%2F1.json", None) == \
        ("https://m/1.json", "https://v.example/x?manifest=https%3A%2F%2Fm%2F1.json")


def test_antenati_manifest_fetches_through_the_waf(tmp_path):
    c = RefererClient(antenati_routes())
    rc = iiif.run(ns(url=MANIFEST, out=str(tmp_path), pages="1"), c)
    assert rc == 0
    assert all(ref == ANTENATI for _, ref, _ in c.calls), c.calls
    got = rows(tmp_path / "manifest.tsv")
    assert [(r["file"], r["manifest_url"]) for r in got] == [("0001_pag-1.jpg", MANIFEST)]


def test_positive_control_the_old_referer_gets_403(tmp_path, monkeypatch):
    """The fake WAF can say no: with the old mapping (the manifest URL as Referer) the run gets 403."""
    monkeypatch.setattr(iiif, "portal_referer", lambda url: None)
    c = RefererClient(antenati_routes())
    with pytest.raises(RuntimeError, match="HTTP 403"):
        iiif.run(ns(url=MANIFEST, out=str(tmp_path), pages="1"), c)
    assert c.calls == [(MANIFEST, MANIFEST, None)]


def test_native_flag_fetches_info_width_not_max(tmp_path):
    c = RefererClient(antenati_routes())
    rc = iiif.run(ns(url=MANIFEST, out=str(tmp_path), pages="1", native=True), c)
    assert rc == 0
    got = rows(tmp_path / "manifest.tsv")
    assert got[0]["image_url"] == "https://iiif-antenati.cultura.gov.it/iiif/2/aAaa111/full/4929,/0/default.jpg"
    assert iiif.pixel_width((tmp_path / "0001_pag-1.jpg").read_bytes()) == 4929


def test_native_after_a_capped_run_adds_the_native_file(tmp_path):
    """Re-running a full/max download with --native in the same folder gets the native width beside it."""
    c = RefererClient(antenati_routes())
    assert iiif.run(ns(url=MANIFEST, out=str(tmp_path), pages="1"), c) == 0
    assert iiif.run(ns(url=MANIFEST, out=str(tmp_path), pages="1", native=True), c) == 0
    assert iiif.pixel_width((tmp_path / "0001_pag-1.jpg").read_bytes()) == 3610       # kept
    assert iiif.pixel_width((tmp_path / "0001_pag-1_native.jpg").read_bytes()) == 4929
    assert [r["file"] for r in rows(tmp_path / "manifest.tsv")] == ["0001_pag-1.jpg", "0001_pag-1_native.jpg"]


def test_native_resume_sends_only_the_manifest_request(tmp_path):
    c = RefererClient(antenati_routes())
    iiif.run(ns(url=MANIFEST, out=str(tmp_path), pages="1", native=True), c)
    n = len(c.calls)
    assert iiif.run(ns(url=MANIFEST, out=str(tmp_path), pages="1", native=True), c) == 0
    assert [u for u, _, _ in c.calls[n:]] == [MANIFEST]


def test_native_list_prints_the_info_json(capsys):
    c = RefererClient(antenati_routes())
    assert iiif.run(ns(url=MANIFEST, pages="1", native=True, list=True), c) == 0
    assert capsys.readouterr().out.strip().endswith("/iiif/2/aAaa111/info.json")


def test_canadiana_viewer_becomes_its_manifest():
    assert iiif.resolve("https://heritage.canadiana.ca/view/oocihm.lac_reel_x0000/5", None)[0] == \
        "https://heritage.canadiana.ca/iiif/oocihm.lac_reel_x0000/manifest"
    assert iiif.resolve("https://heritage.canadiana.ca/view/oocihm.lac_reel_x0000", None)[0] == \
        "https://heritage.canadiana.ca/iiif/oocihm.lac_reel_x0000/manifest"
    m = "https://heritage.canadiana.ca/iiif/oocihm.lac_reel_x0000/manifest"
    assert iiif.resolve(m, None) == (m, m)


def test_lookalike_hosts_are_not_rewritten():
    for url in ("https://evilcanadiana.ca/view/x", "https://canadiana.ca.example/view/x"):
        assert iiif.resolve(url, None) == (url, url)
    port = "https://DAM-ANTENATI.cultura.gov.it:443/antenati/containers/Ex4mple/manifest"
    assert iiif.resolve(port, None) == (port, ANTENATI)


def test_canadiana_run_waits_ten_seconds_between_requests(tmp_path, capsys):
    """Its robots.txt asks for a 10 s crawl delay; the default 1 s is raised, a longer one kept."""
    m = "https://heritage.canadiana.ca/iiif/oocihm.lac_reel_x0000/manifest"
    for given, want in ((1.0, 10.0), (15.0, 15.0)):
        c = FakeClient({m: Resp(json.dumps({"items": []}), "application/json")})
        c.delay = given
        iiif.run(ns(url="https://heritage.canadiana.ca/view/oocihm.lac_reel_x0000", out=str(tmp_path)), c)
        assert c.delay == want
    assert "10 s" in capsys.readouterr().err


# ---------------------------------------------------------------- --image-base

AD = "https://archives.example.fr/iiif/ark:/00000/exemple"


def ad_routes(served_width):
    info = (FIX / "ad_info.json").read_bytes()
    return {
        f"{AD}/f12/info.json": Resp(info, "application/json"),
        f"{AD}/f12/full/3904,/0/default.jpg": Resp(jpeg(served_width), "image/jpeg"),
        f"{AD}/f12/full/max/0/default.jpg": Resp(b"", "text/html", 302),
    }


def test_image_base_fetches_native_width_with_a_row(tmp_path):
    c = FakeClient(ad_routes(3904))
    rc = iiif.run(ns(url=None, image_base=f"{AD}/f12/info.json", out=str(tmp_path)), c)
    assert rc == 0
    got = rows(tmp_path / "manifest.tsv")
    assert [(r["file"], r["image_url"], r["manifest_url"]) for r in got] == [
        ("0001_f12.jpg", f"{AD}/f12/full/3904,/0/default.jpg", f"{AD}/f12/info.json")]
    assert len(got[0]["sha256"]) == 64


def test_image_base_refuses_an_upscaled_file(tmp_path, capsys):
    c = FakeClient(ad_routes(4400))
    rc = iiif.run(ns(url=None, image_base=f"{AD}/f12", out=str(tmp_path)), c)
    assert rc == 1
    assert "4400 px wide, info.json says 3904" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.jpg")) and not (tmp_path / "manifest.tsv").exists()


def test_image_base_refuses_a_downscaled_file(tmp_path):
    rc = iiif.run(ns(url=None, image_base=f"{AD}/f12", out=str(tmp_path)), FakeClient(ad_routes(1600)))
    assert rc == 1 and not list(tmp_path.glob("*.jpg"))


def test_image_base_page_placeholder_and_missing_page(tmp_path):
    c = FakeClient(ad_routes(3904))
    rc = iiif.run(ns(url=None, image_base=AD + "/f{page}/info.json", pages="11-12", out=str(tmp_path)), c)
    assert rc == 1  # f11 has no info.json; f12 is still fetched
    assert [r["file"] for r in rows(tmp_path / "manifest.tsv")] == ["0012_f12.jpg"]
    with pytest.raises(SystemExit, match="needs --pages"):
        iiif.image_bases(AD + "/f{page}", None)
    with pytest.raises(SystemExit, match="give an end"):
        iiif.image_bases(AD + "/f{page}", "3-")


def test_image_base_pages_are_checked():
    with pytest.raises(SystemExit, match="selects no page"):
        iiif.image_bases(AD + "/f{page}", "5-3")
    with pytest.raises(SystemExit, match="no \\{page\\}"):
        iiif.image_bases(AD + "/f12", "1-9")
    with pytest.raises(SystemExit, match="--pages wants"):
        iiif.image_bases(AD + "/f{page}", "a-b")
    assert iiif.image_bases(AD + "/f_{page:04}/info.json", "7,9-10") == [
        (7, AD + "/f_0007"), (9, AD + "/f_0009"), (10, AD + "/f_0010")]
    # a manifest's open-ended range still runs to the last canvas
    assert iiif.parse_pages("2,4-", 5) == [2, 4, 5]


def test_image_base_refuses_labels(tmp_path):
    with pytest.raises(SystemExit, match="--labels"):
        iiif.run(ns(url=None, image_base=f"{AD}/f12", labels="x", out=str(tmp_path)), FakeClient({}))


def test_image_base_resume_sends_no_request(tmp_path):
    c = FakeClient(ad_routes(3904))
    iiif.run(ns(url=None, image_base=f"{AD}/f12", out=str(tmp_path)), c)
    n = len(c.calls)
    assert iiif.run(ns(url=None, image_base=f"{AD}/f12", out=str(tmp_path)), c) == 0
    assert c.calls[n:] == []


def test_info_json_given_as_manifest_points_at_image_base():
    c = FakeClient({f"{AD}/f12/info.json": Resp((FIX / "ad_info.json").read_bytes(), "application/json")})
    with pytest.raises(SystemExit, match="--image-base"):
        iiif.run(ns(url=f"{AD}/f12/info.json"), c)


def test_pixel_width_reads_jpeg_and_png_headers():
    assert iiif.pixel_width(jpeg(3904, 2900)) == 3904
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1234).to_bytes(4, "big") + (99).to_bytes(4, "big")
    assert iiif.pixel_width(png) == 1234
    assert iiif.pixel_width(b"<html>") is None
    PIL = pytest.importorskip("PIL.Image")
    import io
    for kw in ({}, {"progressive": True}):
        buf = io.BytesIO()
        PIL.new("RGB", (777, 55)).save(buf, "JPEG", **kw)
        assert iiif.pixel_width(buf.getvalue()) == 777
