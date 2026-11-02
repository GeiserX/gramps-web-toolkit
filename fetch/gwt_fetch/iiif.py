"""IIIF Presentation v2/v3: resolve a manifest, pick canvases, download each at the largest size served.

Also a bare IIIF Image API base (`--image-base .../info.json`), fetched at the native width that
info.json gives, for archives that serve no Presentation manifest.
"""
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

ANTENATI = "https://antenati.cultura.gov.it/"


def portal_referer(url: str) -> str | None:
    """The Referer a portal's WAF wants, whatever page the URL came from; None when any will do.

    Antenati's manifest server (dam-antenati) and image server (iiif-antenati) answer 403 unless
    the Referer is the portal itself; the manifest URL as Referer is refused too.
    """
    host = urlsplit(url).netloc.lower()
    if host in ("dam-antenati.cultura.gov.it", "iiif-antenati.cultura.gov.it"):
        return ANTENATI
    return None


def resolve(url: str, client) -> tuple[str, str]:
    """Return (manifest_url, referer) for a manifest URL or a viewer URL from a known portal."""
    u = urlsplit(url)
    host = u.netloc.lower()
    q = parse_qs(u.query)
    for key in ("manifest", "iiif-content", "manifestUrl"):
        if key in q:
            manifest = unquote(q[key][0])
            return manifest, portal_referer(manifest) or url
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
    m = host.endswith("canadiana.ca") and re.match(r"/view/([^/?#]+)", u.path)
    if m:  # Canadiana viewer, e.g. heritage.canadiana.ca/view/<id>/<page>
        return f"{u.scheme}://{u.netloc}/iiif/{m.group(1)}/manifest", url
    return url, portal_referer(url) or url


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
    if getattr(args, "image_base", None):
        return run_image_base(args, client)
    if not args.url:
        raise SystemExit("gwt-fetch: give a manifest or viewer URL, or --image-base URL")
    manifest_url, referer = resolve(args.url, client)
    manifest = client.json(manifest_url, referer=referer)
    if _is_image_info(manifest):
        raise SystemExit(f"gwt-fetch: {manifest_url} is a IIIF Image API info.json, not a manifest; "
                         f"use --image-base {manifest_url}")
    cs = select(canvases(manifest), args.pages, args.labels)
    title = _label(manifest.get("label"))
    print(f"gwt-fetch: {title or manifest_url}: {len(cs)} canvas(es) selected", file=sys.stderr)
    if args.list:
        for c in cs:
            url = f"{c.service}/info.json" if getattr(args, "native", False) and c.service else \
                (candidates(c) or [""])[0]
            print(f"{c.index}\t{c.label}\t{url}")
        return 0
    store = Store(args.out)
    failed = 0
    for c in cs:
        stem = f"{c.index:04d}_{slug(c.label, 40)}"
        if getattr(args, "native", False) and c.service:
            failed += not _fetch_native(client, store, stem, c.label, c.service, manifest_url, referer)
            continue
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


# ---------------------------------------------------------------- Image API base, native width

def _is_image_info(doc) -> bool:
    if not isinstance(doc, dict) or "sequences" in doc or "items" in doc:
        return False
    return doc.get("protocol") == "http://iiif.io/api/image" or "/api/image/" in str(doc.get("@context", ""))


def image_bases(spec: str, pages: str | None) -> list[tuple[int, str]]:
    """(position, base URL) for each image. `{page}` in the URL is replaced by each --pages number."""
    base = re.sub(r"/info\.json$", "", spec.strip()).rstrip("/")
    if "{page}" not in base:
        return [(1, base)]
    if not pages:
        raise SystemExit("gwt-fetch: --image-base with {page} needs --pages, e.g. --pages 1-20")
    want: list[int] = []
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            if not b:
                raise SystemExit("gwt-fetch: --image-base cannot count the pages; give an end, e.g. 12-40")
            want.extend(range(int(a), int(b) + 1))
        elif part:
            want.append(int(part))
    return [(n, base.replace("{page}", str(n))) for n in dict.fromkeys(want)]


def pixel_width(data: bytes) -> int | None:
    """Width in pixels of a JPEG or PNG, read from its header; None for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big")
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # markers without a length
            i += 2
            continue
        size = int.from_bytes(data[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # start of frame
            return int.from_bytes(data[i + 7:i + 9], "big")
        i += 2 + size
    return None


NATIVE_URL = re.compile(r"/full/\d+,/0/default\.jpg$")


def _fetch_native(client, store: Store, stem: str, label: str, base: str, source: str, referer: str) -> bool:
    """Fetch one image at the width its info.json gives. Refuse (write nothing) a file of another width.

    A file already saved at native width costs no request. One saved earlier from full/max (a plain
    run into the same folder) is kept, and the native image goes beside it as <stem>_native.
    """
    p = store.find(stem)
    row = store.rows.get(p.name) if p else None
    if row and not NATIVE_URL.search(row["image_url"]):
        stem += "_native"
        p = store.find(stem)
        row = store.rows.get(p.name) if p else None
    if row:
        return True
    try:
        width = int(client.json(f"{base}/info.json", referer=referer)["width"])
    except Exception as e:  # a missing page or a broken info.json fails this image, not the run
        print(f"gwt-fetch: {label}: {base}/info.json: {e.__class__.__name__}: {e}", file=sys.stderr)
        return False
    url = f"{base}/full/{width},/0/default.jpg"
    if store.done(stem, label, url, source):
        return True
    r = client.get(url, referer=referer)
    ctype = r.headers.get("Content-Type", "")
    if not r.ok or ctype.startswith("text/"):
        print(f"gwt-fetch: {label}: {url} -> {r.status_code} {ctype}", file=sys.stderr)
        r.close()
        return False
    got = pixel_width(r.content)
    if got != width:
        print(f"gwt-fetch: {label}: {url} is {got} px wide, info.json says {width}; not saved "
              f"(the server rescaled it)", file=sys.stderr)
        return False
    p = store.write(stem, [r.content], ctype, label, url, source)
    print(f"{p}", file=sys.stderr)
    return True


def run_image_base(args, client) -> int:
    bases = image_bases(args.image_base, args.pages)
    print(f"gwt-fetch: {len(bases)} image(s) from a IIIF Image API base", file=sys.stderr)
    if args.list:
        for n, base in bases:
            print(f"{n}\t{base.rsplit('/', 1)[-1]}\t{base}/info.json")
        return 0
    store = Store(args.out)
    failed = 0
    for n, base in bases:
        label = base.rsplit("/", 1)[-1]
        referer = portal_referer(base) or f"{base}/info.json"
        ok = _fetch_native(client, store, f"{n:04d}_{slug(label, 40)}", label, base, f"{base}/info.json", referer)
        failed += not ok
    if store.manifest.exists():
        print(f"gwt-fetch: manifest rows in {store.manifest}", file=sys.stderr)
    return 1 if failed else 0
