"""BOE «Gazeta» colección histórica (Gaceta de Madrid and its predecessors, 1661-1959).

The BOE open data API covers only the modern BOE, so this drives the public search form
(`/buscar/gazeta.php`) and the per-reference document page (`/buscar/doc.php?id=BOE-A-YYYY-N`),
whose «PDF» link is the scanned page of the original issue.
"""
from __future__ import annotations

import html as htmllib
import re
import shutil
import subprocess
import sys
from urllib.parse import urljoin

from .store import Store

BASE = "https://www.boe.es"
SEARCH = BASE + "/buscar/gazeta.php"
DOC = BASE + "/buscar/doc.php?id="
REF = re.compile(r"^BOE-[A-Z]-\d{4}-\d+$")
HITS = (50, 200, 500, 1000, 2000)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def search_params(text=None, title=None, date_from=None, date_to=None, gazette=None, limit=50) -> list:
    p = [("campo[0]", "ID_HIST"), ("dato[0]", ""),
         ("campo[1]", "TITULOS"), ("dato[1]", title or ""), ("operador[1]", "and"),
         ("campo[4]", "DOC"), ("dato[4]", text or ""), ("operador[4]", "and"),
         ("campo[6]", "GAZ.ID"), ("dato[6]", gazette or ""),
         ("campo[8]", "FPU"), ("dato[8][0]", date_from or ""), ("dato[8][1]", date_to or ""), ("operador[8]", "and"),
         ("page_hits", str(next((h for h in HITS if h >= limit), HITS[-1]))),
         ("sort_field[0]", "FPU"), ("sort_order[0]", "asc"),
         ("sort_field[1]", "REF"), ("sort_order[1]", "asc"),
         ("accion", "Buscar")]
    return p


def parse_results(page: str) -> list[dict]:
    rows = []
    for li in re.findall(r'<li class="resultado-busqueda">(.*?)</ul>\s*</li>', page, re.S):
        title = re.search(r'<p class="linea-pub">(.*?)</p>', li, re.S)
        dts = dict((_text(k).rstrip(":"), _text(v)) for k, v in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", li, re.S))
        pdf = re.search(r'href="([^"]+\.pdf)"[^>]*Ref\. (BOE-[A-Z]-\d{4}-\d+)', li)
        where = next(iter(dts.items()), ("", ""))
        num = re.search(r"núm\. (\S+), de (\d\d)/(\d\d)/(\d{4})(?:, páginas? (\d+(?: a \d+)?))?", where[1])
        issue = issue_pdf = ""
        if num and where[0] == "Gaceta de Madrid":  # whole issue: GMD-<year>-<number>, filed by date
            y, m, d, n = num.group(4), num.group(3), num.group(2), num.group(1)
            issue = f"GMD-{y}-{n}"
            issue_pdf = f"{BASE}/gazeta/dias/{y}/{m}/{d}/pdfs/{issue}.pdf"
        rows.append({
            "ref": pdf.group(2) if pdf else "",
            "date": f"{num.group(4)}-{num.group(3)}-{num.group(2)}" if num else "",
            "gazette": where[0],
            "number": num.group(1) if num else "",
            "page": (num.group(5) or "") if num else "",
            "department": dts.get("Departamento", ""),
            "title": _text(title.group(1)) if title else "",
            "pdf_url": urljoin(BASE, pdf.group(1)) if pdf else "",
            "issue": issue,
            "issue_pdf": issue_pdf,
        })
    return rows


def search(client, **kw) -> list[dict]:
    page = client.get(SEARCH, params=search_params(**kw))
    page.raise_for_status()
    return parse_results(page.text)[: kw.get("limit") or None]


def parse_doc(page: str) -> dict:
    title = re.search(r'<h3 class="documento-tit">(.*?)</h3>', page, re.S)
    pub = re.search(r"<dt>Publicado en:</dt>\s*<dd>(.*?)</dd>", page, re.S)
    pdf = re.search(r'<li class="puntoPDF">\s*<a[^>]*href="([^"]+\.pdf)"', page)
    body = re.search(r'<div id="textoxslt">(.*?)</div>\s*<!-- #textoxslt -->', page, re.S)
    text = ""
    if body and "Texto no disponible" not in body.group(1):
        text = "\n\n".join(_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", body.group(1), re.S))
    return {"title": _text(title.group(1)) if title else "", "published": _text(pub.group(1)) if pub else "",
            "pdf_url": urljoin(BASE, pdf.group(1)) if pdf else "", "text": text}


def fetch(client, ref: str, store: Store) -> int:
    if not REF.match(ref):
        print(f"gwt-fetch: {ref} is not a Gazeta reference like BOE-A-1870-1111", file=sys.stderr)
        return 1
    url = DOC + ref
    doc = parse_doc(client.text(url))
    if not doc["pdf_url"]:
        print(f"gwt-fetch: no PDF on {url}", file=sys.stderr)
        return 1
    label = f"{doc['title']} | {doc['published']}"
    pdf = store.done(ref, label, doc["pdf_url"], url)
    if not pdf:
        r = client.get(doc["pdf_url"], referer=url, stream=True)
        r.raise_for_status()
        pdf = store.write(ref, r.iter_content(1 << 16), r.headers.get("Content-Type", "application/pdf"),
                          label, doc["pdf_url"], url)
    print(pdf, file=sys.stderr)
    stem = ref + "_text"
    if store.find(stem):
        return 0
    text, source = doc["text"], url
    if not text and shutil.which("pdftotext"):
        # The scans carry the BOE's own OCR layer; it is rough on 19th-century type, but searchable.
        text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
        source = doc["pdf_url"]
    if text.strip():
        store.write(stem, [text.encode("utf-8")], "text/plain", label + " (text)", source, url)
    return 0


COLUMNS = ["ref", "date", "gazette", "number", "page", "department", "title", "pdf_url", "issue", "issue_pdf"]


def run(args, client) -> int:
    if args.action == "search":
        if not (args.text or args.title):
            raise SystemExit("gwt-fetch: gazeta search needs --text or --title")
        rows = search(client, text=args.text, title=args.title, date_from=args.date_from,
                      date_to=args.date_to, gazette=args.gazette, limit=args.limit)
        print("\t".join(COLUMNS))
        for r in rows:
            print("\t".join(r[c].replace("\t", " ") for c in COLUMNS))
        print(f"gwt-fetch: {len(rows)} result(s)", file=sys.stderr)
        return 0
    store = Store(args.out)
    return max([fetch(client, ref, store) for ref in args.refs] or [0])
