"""Offline tests: a fake client answers from fixtures, so nothing touches the network."""
import argparse
import csv
import io
import json

import pytest

from gwt_fetch import digibib, europeana, gazeta, geo, iiif
from gwt_fetch.store import Store, slug


class Resp:
    def __init__(self, body=b"", ctype="text/html", status=200, url=""):
        self.content = body if isinstance(body, bytes) else body.encode()
        self.headers = {"Content-Type": ctype}
        self.status_code = status
        self.ok = status < 400
        self.url = url

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return json.loads(self.content)

    def iter_content(self, n):
        yield self.content

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        pass


class FakeClient:
    def __init__(self, routes):
        self.routes = routes  # url (or url prefix) -> Resp or callable
        self.calls = []

    def get(self, url, referer=None, stream=False, params=None, **kw):
        self.calls.append((url, referer, params))
        for key, val in self.routes.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                r = val(url, params) if callable(val) else val
                r.url = r.url or url
                return r
        return Resp(b"not found", status=404, url=url)

    def json(self, url, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()

    def text(self, url, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.text


def ns(**kw):
    base = dict(pages=None, labels=None, out=".", list=False)
    base.update(kw)
    return argparse.Namespace(**base)


def rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


# ---------------------------------------------------------------- store

def test_slug_folds_accents():
    assert slug("Pàgina 1 · Año") == "pagina-1-ano"
    assert slug("") == "x"


def test_store_never_overwrites_and_records_orphans(tmp_path):
    s = Store(tmp_path)
    p = s.write("0001_a", [b"one"], "image/jpeg", "a", "http://i/1", "http://m")
    assert p.name == "0001_a.jpg"
    s.write("0001_a", [b"two"], "image/jpeg", "a", "http://i/1", "http://m")
    assert p.read_bytes() == b"one"
    # a finished file with no row (run died before the append) gets its row on the next run
    (tmp_path / "0002_b.jpg").write_bytes(b"x")
    s2 = Store(tmp_path)
    assert s2.done("0002_b", "b", "http://i/2", "http://m").name == "0002_b.jpg"
    assert [r["file"] for r in rows(tmp_path / "manifest.tsv")] == ["0001_a.jpg", "0002_b.jpg"]
    assert not s2.done("0003_c", "c", "", "")


def test_store_ignores_part_files(tmp_path):
    (tmp_path / "0001_a.jpg.part").write_bytes(b"half")
    assert Store(tmp_path).find("0001_a") is None


# ---------------------------------------------------------------- iiif

V2 = {
    "@context": "http://iiif.io/api/presentation/2/context.json",
    "label": "Archivio > Comune > 1870",
    "sequences": [{"canvases": [
        {"label": "pag. 1", "images": [{"resource": {"@id": "https://img/iiif/2/A/full/full/0/default.jpg",
                                                     "service": {"@id": "https://img/iiif/2/A",
                                                                 "@context": "http://iiif.io/api/image/2/context.json"}}}]},
        {"label": "pag. 2", "images": [{"resource": {"@id": "https://img/iiif/2/B/full/full/0/default.jpg",
                                                     "service": {"@id": "https://img/iiif/2/B/"}}}]},
        {"label": "copertina", "images": [{"resource": {"@id": "https://img/static/C.jpg"}}]},
    ]}],
}
V3 = {
    "@context": "http://iiif.io/api/presentation/3/context.json",
    "label": {"es": ["Ferrol"]},
    "items": [
        {"label": {"none": ["p. 1"]}, "items": [{"items": [{"body": {
            "id": "https://api.europeana.eu/thumbnail/v2/url.json?uri=https%3A%2F%2Forig%2Fimg%3Fid%3D7&type=TEXT",
            "type": "Image"}}]}]},
        {"label": {"es": ["p. 2"]}, "items": [{"items": [{"body": {
            "id": "https://x/full.jpg", "type": "Image",
            "service": [{"id": "https://x/iiif3/Z", "type": "ImageService3"}]}}]}]},
    ],
}


def test_canvases_v2_and_candidates():
    cs = iiif.canvases(V2)
    assert [c.label for c in cs] == ["pag. 1", "pag. 2", "copertina"]
    assert iiif.candidates(cs[1])[:2] == ["https://img/iiif/2/B/full/max/0/default.jpg",
                                          "https://img/iiif/2/B/full/full/0/default.jpg"]
    assert iiif.candidates(cs[2]) == ["https://img/static/C.jpg"]


def test_canvases_v3_unwraps_europeana_thumbnail_and_v3_uses_max_only():
    cs = iiif.canvases(V3)
    assert iiif.candidates(cs[0]) == ["https://orig/img?id=7"]
    assert iiif.candidates(cs[1]) == ["https://x/iiif3/Z/full/max/0/default.jpg", "https://x/full.jpg"]


def test_select_pages_and_labels():
    cs = iiif.canvases(V2)
    assert [c.index for c in iiif.select(cs, "1,3", None)] == [1, 3]
    assert [c.index for c in iiif.select(cs, "2-", None)] == [2, 3]
    assert [c.index for c in iiif.select(cs, None, r"^pag")] == [1, 2]


def test_resolve_known_viewers():
    c = FakeClient({"https://antenati.cultura.gov.it/ark:/12657/an_ua1/": Resp(
        '<a href="https://dam-antenati.cultura.gov.it/antenati/containers/abc/manifest">x</a>')})
    assert iiif.resolve("https://antenati.cultura.gov.it/ark:/12657/an_ua1/", c) == (
        "https://dam-antenati.cultura.gov.it/antenati/containers/abc/manifest", "https://antenati.cultura.gov.it/")
    assert iiif.resolve("https://mdc.csuc.cat/digital/collection/comercUPF/id/22725", c)[0] == \
        "https://mdc.csuc.cat/iiif/2/comercUPF:22725/manifest.json"
    assert iiif.resolve("https://www.europeana.eu/es/item/2022719/abc_ent0", c)[0] == \
        "https://iiif.europeana.eu/presentation/2022719/abc_ent0/manifest"
    assert iiif.resolve("https://viewer/x?manifest=https%3A%2F%2Fm%2F1.json", c)[0] == "https://m/1.json"
    assert iiif.resolve("https://m/2.json", c)[0] == "https://m/2.json"


def test_iiif_run_falls_back_writes_manifest_and_resumes(tmp_path):
    routes = {
        "https://m/manifest": Resp(json.dumps(V2), "application/json"),
        "https://img/iiif/2/A/full/max/0/default.jpg": Resp(b"A-max", "image/jpeg"),
        "https://img/iiif/2/B/full/max/0/default.jpg": Resp(b"denied", "text/html", 403),
        "https://img/iiif/2/B/full/full/0/default.jpg": Resp(b"B-full", "image/jpeg"),
        "https://img/static/C.jpg": Resp(b"blocked page", "text/html", 200),
    }
    c = FakeClient(routes)
    rc = iiif.run(ns(url="https://m/manifest", out=str(tmp_path)), c)
    assert rc == 1  # canvas 3 only answered with HTML
    got = rows(tmp_path / "manifest.tsv")
    assert [(r["file"], r["image_url"]) for r in got] == [
        ("0001_pag-1.jpg", "https://img/iiif/2/A/full/max/0/default.jpg"),
        ("0002_pag-2.jpg", "https://img/iiif/2/B/full/full/0/default.jpg")]
    assert got[0]["manifest_url"] == "https://m/manifest" and len(got[0]["sha256"]) == 64
    n = len(c.calls)
    iiif.run(ns(url="https://m/manifest", out=str(tmp_path), pages="1-2"), c)
    assert len(c.calls) == n + 1  # only the manifest again; finished files are skipped


# ---------------------------------------------------------------- digibib

def test_digibib_pages_and_challenge(tmp_path):
    base = "https://bv.example/es/catalogo_imagenes/grupo.do"
    def page(url, params):
        if "presentacion=pagina" in url:
            n = url.split("posicion=")[1].split("&")[0]
            return Resp(f"<img src=\"../catalogo_imagenes/imagen_id.do?idImagen=90{n}&formato=jpg\">")
        return Resp("<title>BV &gt; Diario 1900</title><a href='x?numPags=3'>")
    c = FakeClient({base + "*": page,
                    "https://bv.example/es/catalogo_imagenes/imagen_id.do*": Resp(b"jpg", "image/jpeg")})
    assert digibib.run(ns(url=base + "?path=5", out=str(tmp_path), pages="2-3"), c) == 0
    got = rows(tmp_path / "manifest.tsv")
    assert [r["file"] for r in got] == ["0002_p-2.jpg", "0003_p-3.jpg"]
    assert "idImagen=902&formato=jpg" in got[0]["image_url"] and got[0]["label"] == "BV > Diario 1900 p. 2"

    blocked = FakeClient({base + "*": Resp("<script>eval(function(p,a,c,k,e,d){...})</script>")})
    with pytest.raises(SystemExit, match="JavaScript challenge"):
        digibib.run(ns(url=base + "?path=5", out=str(tmp_path / "b")), blocked)


def test_digibib_single_file_object(tmp_path):
    url = "https://arq.example/gl/catalogo_imagenes/grupo.do?path=9"
    c = FakeClient({url: Resp(b"%PDF-1.7", "application/pdf")})
    assert digibib.run(ns(url=url, out=str(tmp_path)), c) == 0
    assert (tmp_path / "0001_document.pdf").read_bytes() == b"%PDF-1.7"


# ---------------------------------------------------------------- gazeta

RESULTS = """
<ul><li class="resultado-busqueda">
  <p class="linea-pub">Dirección general de Comunicaciones.- Subasta del correo entre Betanzos y Ferrol.</p>
  <div class="datosGaz"><dl class="nosize">
    <dt>Gaceta de Madrid:</dt><dd><abbr title="número">núm.</abbr> 40, de 09/02/1870, página 2</dd>
    <dt>Departamento:</dt><dd>Ministerio de la Gobernación</dd></dl></div>
  <ul><li class="puntoPDF2"><a href="/datos/pdfs/BOE//1870/040/A00002-00002.pdf" title="Documento PDF (Ref. BOE-A-1870-1111)">PDF</a></li>
  </ul>
</li>
<li class="resultado-busqueda">
  <p class="linea-pub">Junta Calificadora.- Relación de vacantes.</p>
  <div class="datosGaz"><dl class="nosize">
    <dt>Gaceta de Madrid:</dt><dd><abbr title="número">núm.</abbr> 43, de 12/02/1890, páginas 436 a 440</dd>
    <dt>Departamento:</dt><dd>Ministerio de Guerra</dd></dl></div>
  <ul><li class="puntoPDF2"><a href="/datos/pdfs/BOE//1890/043/A00436-00440.pdf" title="Documento PDF (Ref. BOE-A-1890-910)">PDF</a></li>
  </ul>
</li></ul>"""

DOCPAGE = """<h3 class="documento-tit">Dirección general de Comunicaciones.- Subasta.</h3>
<dl><dt>Publicado en:</dt>
<dd>«Gaceta de Madrid» <abbr title="número">núm.</abbr> 40, de 9 de febrero de 1870, páginas 2 a 2</dd></dl>
<li class="puntoPDF">
  <a title="Documento PDF" href="/datos/pdfs/BOE//1870/040/A00002-00002.pdf">PDF</a></li>
<div id="textoxslt">
  <p class="parrafo">Primera línea.</p><p class="parrafo">Segunda &aacute;.</p>
</div>
<!-- #textoxslt -->"""


def test_gazeta_parse_results():
    r = gazeta.parse_results(RESULTS)
    assert r[0] == {"ref": "BOE-A-1870-1111", "date": "1870-02-09", "gazette": "Gaceta de Madrid", "number": "40",
                    "page": "2", "department": "Ministerio de la Gobernación",
                    "title": "Dirección general de Comunicaciones.- Subasta del correo entre Betanzos y Ferrol.",
                    "pdf_url": "https://www.boe.es/datos/pdfs/BOE//1870/040/A00002-00002.pdf",
                    "issue": "GMD-1870-40",
                    "issue_pdf": "https://www.boe.es/gazeta/dias/1870/02/09/pdfs/GMD-1870-40.pdf"}
    assert r[1]["page"] == "436 a 440" and r[1]["ref"] == "BOE-A-1890-910"


def test_gazeta_search_params():
    p = dict(gazeta.search_params(text="Betanzos", date_from="1870-01-01", limit=120))
    assert p["dato[4]"] == "Betanzos" and p["dato[8][0]"] == "1870-01-01" and p["page_hits"] == "200"


def test_gazeta_fetch_writes_pdf_and_html_text(tmp_path):
    c = FakeClient({gazeta.DOC + "BOE-A-1870-1111": Resp(DOCPAGE),
                    "https://www.boe.es/datos/pdfs/BOE//1870/040/A00002-00002.pdf": Resp(b"%PDF", "application/pdf")})
    assert gazeta.fetch(c, "BOE-A-1870-1111", Store(tmp_path)) == 0
    assert (tmp_path / "BOE-A-1870-1111.pdf").read_bytes() == b"%PDF"
    assert (tmp_path / "BOE-A-1870-1111_text.txt").read_text() == "Primera línea.\n\nSegunda á."
    assert [r["file"] for r in rows(tmp_path / "manifest.tsv")] == ["BOE-A-1870-1111.pdf", "BOE-A-1870-1111_text.txt"]
    assert gazeta.fetch(c, "not-a-ref", Store(tmp_path)) == 1


# ---------------------------------------------------------------- europeana

def test_europeana_skips_without_key(monkeypatch, capsys):
    monkeypatch.delenv("EUROPEANA_KEY", raising=False)
    assert europeana.run(argparse.Namespace(query="x"), FakeClient({})) == 0
    assert "EUROPEANA_KEY is not set" in capsys.readouterr().err


def test_europeana_params_and_row():
    p = europeana.params("k", "Ferrol", "Hispana", None, None, "1800", None, True, 20)
    assert ("qf", 'PROVIDER:"Hispana"') in p and ("qf", "YEAR:[1800 TO *]") in p
    assert ("qf", "sv_dcterms_conformsTo:*iiif*") in p
    r = europeana.row({"id": "/2022706/abc", "title": ["Le Ferrol"], "dataProvider": ["Galiciana"], "year": ["1880"]})
    assert r["manifest"] == "https://iiif.europeana.eu/presentation/2022706/abc/manifest"
    assert r["title"] == "Le Ferrol"


def test_europeana_run_prints_tsv(monkeypatch, capsys):
    monkeypatch.setenv("EUROPEANA_KEY", "k")
    body = {"success": True, "totalResults": 1, "items": [{"id": "/1/a", "title": ["T"]}]}
    c = FakeClient({europeana.API: Resp(json.dumps(body), "application/json")})
    args = argparse.Namespace(query="q", provider="Hispana", data_provider=None, country=None,
                              year_from=None, year_to=None, iiif_only=False, rows=5)
    assert europeana.run(args, c) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("id\ttitle") and out[1].startswith("/1/a\tT")


# ---------------------------------------------------------------- geo

def test_geo_filters_types_and_province():
    cands = [
        {"id": "1", "type": "callejero", "address": "RUA X", "muni": "Betanzos", "muniCode": "15009", "province": "A Coruña"},
        {"id": "15009", "type": "Municipio", "address": "Betanzos, A Coruña", "muni": "Betanzos",
         "muniCode": "15009", "province": "A Coruña"},
        {"id": "99", "type": "Municipio", "address": "Betanzos, Lugo", "muni": "Betanzos", "muniCode": "27000",
         "province": "Lugo"},
    ]
    c = FakeClient({geo.API + "/candidates": Resp(json.dumps(cands), "application/json"),
                    geo.API + "/find": Resp(json.dumps({"lat": 43.2752331, "lng": -8.2231386}), "application/json")})
    out = geo.geocode(c, "Betanzos", ["Municipio"], province="a coruna")
    assert out == [{"query": "Betanzos", "type": "Municipio", "name": "Betanzos, A Coruña", "municipality": "Betanzos",
                    "ine_code": "15009", "province": "A Coruña", "lat": "43.275233", "lng": "-8.223139",
                    "cartociudad_id": "15009"}]
