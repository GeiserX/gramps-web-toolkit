"""IIIF Presentation v2/v3: resolve a manifest, pick canvases, download each at the largest size served."""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from .store import Store, slug


@dataclass
class Canvas:
    index: int          # 1-based position in the manifest
    label: str
    service: str        # Image API base URL, or "" when the canvas only has a static image
    service_v3: bool
    image: str          # the static image URL from the manifest (fallback)


# ---------------------------------------------------------------- viewer URL -> manifest

def resolve(url: str, client) -> tuple[str, str]:
    """Return (manifest_url, referer) for a manifest URL or a viewer URL from a known portal."""
    u = urlsplit(url)
    host = u.netloc.lower()
    q = parse_qs(u.query)
    for key in ("manifest", "iiif-content", "manifestUrl"):
        if key in q:
            return unquote(q[key][0]), url
    if host.endswith("antenati.cultura.gov.it") and "/ark:/" in u.path:
        html = client.text(url)
        m = re.search(r"https://dam-antenati\.cultura\.gov\.it/antenati/containers/\w+/manifest", html)
        if not m:
            raise SystemExit(f"gwt-fetch: no manifest link on the Antenati page {url}")
        return m.group(0), "https://antenati.cultura.gov.it/"
    m = re.search(r"/digital/collection/([^/]+)/id/(\d+)", u.path)
    if m:  # any CONTENTdm site, e.g. Memòria Digital de Catalunya (mdc.csuc.cat)
        return f"{u.scheme}://{u.netloc}/iiif/2/{m.group(1)}:{m.group(2)}/manifest.json", url
    m = re.search(r"europeana\.eu$", host) and re.search(r"/item/(\d+)/([^/?#]+)", u.path)
    if m:
        return f"https://iiif.europeana.eu/presentation/{m.group(1)}/{m.group(2)}/manifest", url
    return url, url


# ---------------------------------------------------------------- manifest parsing

def _label(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return " ".join(_label(x) for x in v)
    if isinstance(v, dict):
        if "@value" in v:
            return str(v["@value"])
        for lang in ("es", "it", "ca", "gl", "eu", "en", "none", "@none"):
            if lang in v:
                return _label(v[lang])
        return _label(next(iter(v.values()), ""))
    return ""


def _service(obj) -> tuple[str, bool]:
    svc = obj.get("service")
    if isinstance(svc, list):
        svc = svc[0] if svc else None
    if not isinstance(svc, dict):
        return "", False
    sid = svc.get("@id") or svc.get("id") or ""
    v3 = svc.get("type") == "ImageService3" or "image/3" in str(svc.get("@context", ""))
    return sid.rstrip("/"), v3


def canvases(manifest: dict) -> list[Canvas]:
    out: list[Canvas] = []
    if "sequences" in manifest:  # v2
        items = (manifest["sequences"] or [{}])[0].get("canvases", [])
        for i, c in enumerate(items, 1):
            res = ((c.get("images") or [{}])[0]).get("resource", {}) or {}
            sid, v3 = _service(res)
            out.append(Canvas(i, _label(c.get("label")), sid, v3, res.get("@id", "")))
    else:  # v3
        for i, c in enumerate(manifest.get("items", []), 1):
            body = {}
            for page in c.get("items", []):
                for anno in page.get("items", []):
                    body = anno.get("body", {}) or {}
                    if isinstance(body, list):
                        body = body[0] if body else {}
                    break
                break
            sid, v3 = _service(body)
            out.append(Canvas(i, _label(c.get("label")), sid, v3, body.get("id", "")))
    return out


def select(cs: list[Canvas], pages: str | None, labels: str | None) -> list[Canvas]:
    if pages:
        want: set[int] = set()
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                want.update(range(int(a), int(b or len(cs)) + 1))
            elif part:
                want.add(int(part))
        cs = [c for c in cs if c.index in want]
    if labels:
        rx = re.compile(labels, re.I)
        cs = [c for c in cs if rx.search(c.label)]
    return cs


def candidates(c: Canvas) -> list[str]:
    """Image URLs to try, best first: the largest size the Image API serves, then the static image."""
    urls = []
    if c.service:
        urls.append(f"{c.service}/full/max/0/default.jpg")
        if not c.service_v3:
            urls.append(f"{c.service}/full/full/0/default.jpg")
    if c.image:
        urls.append(_unwrap_thumbnail(c.image))
    return list(dict.fromkeys(urls))


def _unwrap_thumbnail(url: str) -> str:
    """Europeana builds manifests whose image is its own thumbnail proxy; take the original instead."""
    u = urlsplit(url)
    if u.netloc == "api.europeana.eu" and u.path.startswith("/thumbnail/"):
        q = parse_qs(u.query)
        if "uri" in q:
            return q["uri"][0]
    return url


# ---------------------------------------------------------------- command

def run(args, client) -> int:
    manifest_url, referer = resolve(args.url, client)
    manifest = client.json(manifest_url, referer=referer)
    cs = select(canvases(manifest), args.pages, args.labels)
    title = _label(manifest.get("label"))
    print(f"gwt-fetch: {title or manifest_url}: {len(cs)} canvas(es) selected", file=sys.stderr)
    if args.list:
        for c in cs:
            print(f"{c.index}\t{c.label}\t{(candidates(c) or [''])[0]}")
        return 0
    store = Store(args.out)
    failed = 0
    for c in cs:
        stem = f"{c.index:04d}_{slug(c.label, 40)}"
        urls = candidates(c)
        if store.done(stem, c.label, urls[0] if urls else "", manifest_url):
            continue
        tried = []
        for url in urls:
            r = client.get(url, referer=referer, stream=True)
            ctype = r.headers.get("Content-Type", "")
            if r.ok and not ctype.startswith("text/"):
                p = store.write(stem, r.iter_content(1 << 16), ctype, c.label, url, manifest_url)
                print(f"{p}", file=sys.stderr)
                break
            tried.append(f"{url} -> {r.status_code} {ctype}")
            r.close()
        else:
            failed += 1
            print(f"gwt-fetch: canvas {c.index} ({c.label}) failed: {'; '.join(tried)}", file=sys.stderr)
    if store.manifest.exists():
        print(f"gwt-fetch: manifest rows in {store.manifest}", file=sys.stderr)
    return 1 if failed else 0
