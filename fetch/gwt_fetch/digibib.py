"""DIGIBIB viewers (BVPH, Biblioteca Virtual de Defensa, Galiciana and other DIGIBÍS sites).

These portals serve no IIIF. Their viewer is `.../catalogo_imagenes/grupo.do?path=<id>`; each page view
(`&presentacion=pagina&posicion=<n>`) names one image as `imagen_id.do?idImagen=<n>`, and that URL
with `&formato=jpg` returns the full JPEG.
"""
from __future__ import annotations

import html
import re
import sys
from urllib.parse import parse_qs, urljoin, urlsplit

from .iiif import select, Canvas
from .store import Store

CHALLENGE = "eval(function(p,a,c,k,e,d)"


def _page(client, url: str, page: str | None = None) -> str:
    page = client.text(url) if page is None else page
    if CHALLENGE in page[:4000]:
        raise SystemExit(f"gwt-fetch: {urlsplit(url).netloc} answers with a JavaScript challenge page; "
                         "this portal needs a browser")
    return page


def run(args, client) -> int:
    u = urlsplit(args.url)
    path = (parse_qs(u.query).get("path") or [None])[0]
    if "grupo.do" not in u.path or not path:
        raise SystemExit("gwt-fetch: expected a DIGIBIB viewer URL like .../catalogo_imagenes/grupo.do?path=12345")
    base = urljoin(args.url, "grupo.do")
    r = client.get(f"{base}?path={path}", stream=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "")
    if not ctype.startswith("text/"):  # a one-file object: the viewer URL serves the file itself (often a PDF)
        if args.list:
            print(f"1\tdocument\t{r.url}")
        else:
            store = Store(args.out)
            if not store.done("0001_document", path, r.url, args.url):
                print(store.write("0001_document", r.iter_content(1 << 16), ctype, path, r.url, args.url), file=sys.stderr)
        return 0
    first = _page(client, r.url, r.text)
    m = re.search(r"numPags=(\d+)", first)
    total = int(m.group(1)) if m else max([int(x) for x in re.findall(r"posicion=(\d+)", first)] or [1])
    title = re.search(r"<title>(.*?)</title>", first, re.S)
    title = re.sub(r"\s+", " ", html.unescape(title.group(1))).strip() if title else path
    pages = select([Canvas(i, f"p. {i}", "", False, "") for i in range(1, total + 1)], args.pages, None)
    print(f"gwt-fetch: {title}: {total} page(s), {len(pages)} selected", file=sys.stderr)
    store = None if args.list else Store(args.out)
    failed = 0
    for pg in pages:
        view = f"{base}?path={path}&presentacion=pagina&posicion={pg.index}&registrardownload=0"
        stem = f"{pg.index:04d}_p-{pg.index}"
        if store and store.done(stem, pg.label, "", view):
            continue
        m = re.search(r"imagen_id\.do\?idImagen=(\d+)", _page(client, view))
        if not m:
            failed += 1
            print(f"gwt-fetch: no image on page {pg.index} ({view})", file=sys.stderr)
            continue
        img = urljoin(view, f"imagen_id.do?idImagen={m.group(1)}&formato=jpg&registrardownload=0")
        if args.list:
            print(f"{pg.index}\t{pg.label}\t{img}")
            continue
        r = client.get(img, referer=view, stream=True)
        ctype = r.headers.get("Content-Type", "")
        if not r.ok or not ctype.startswith("image/"):
            failed += 1
            print(f"gwt-fetch: HTTP {r.status_code} {ctype} for {img}", file=sys.stderr)
            r.close()
            continue
        print(store.write(stem, r.iter_content(1 << 16), ctype, f"{title} {pg.label}", img, view), file=sys.stderr)
    return 1 if failed else 0
