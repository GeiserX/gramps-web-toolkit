"""Media that Gramps Web cannot show: files it draws as a grey icon, files missing on the server, and, with
`--download`, images too small to read, mostly one tone, or the same page twice on one citation.

What the server says about each media object's file is kept beside the objects, in the dump under STATUS, so a
saved dump carries it and an offline check of that dump reports the same findings. The objects themselves stay
as the server sent them: gwt-apply writes objects from a dump back to the server. Pillow is optional: without
it the image kinds are skipped with a note, never a crash.
"""

from __future__ import annotations

import mimetypes
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from .client import ApiError

# dump key: {media handle: {"file_missing": bool}} from a live read, plus "w", "h", "tone", "dhash" with --download
STATUS = "media_status"

# Gramps Web draws a thumbnail for images and PDFs. These others it shows as a grey icon that opens nothing useful
NON_VIEWABLE_PREFIXES = ("text/", "message/")
NON_VIEWABLE = {"application/json", "application/xhtml+xml", "application/x-tar", "application/gzip",
                "application/x-gzip", "application/zip"}
THUMB = 256         # px: the thumbnail asked for; the server never enlarges, so a smaller one is the real size
MIN_SIDE = 200      # px: an image whose longer side is under this is too small to read
TONE_WINDOW = 12    # grey levels either side of the most common one that count as the same tone
UNIFORM_SHARE = 0.98  # share of pixels in one tone that makes a blank, grey or failed image
HASH_SIDE = 16      # dHash of 16 x 16 = 256 bits
DUP_BITS = 20       # bits apart at most for two images on one citation to be the same page

# found from what the server says after the read, not from the objects: never part of the pass comparison
AFTER_READ = {"media-file-missing", "media-tiny", "media-uniform", "media-dup-page"}


def _mime(o: dict) -> str:
    """The stored MIME type, or the one its file name gives when none is stored."""
    return (o.get("mime") or mimetypes.guess_type(o.get("path") or "")[0] or "").lower()


def non_viewable(o: dict) -> str | None:
    """The MIME type of a media object Gramps Web shows only as an icon, or None."""
    mime = _mime(o)
    if mime.startswith(NON_VIEWABLE_PREFIXES) or mime in NON_VIEWABLE:
        return mime
    return None


def is_image(o: dict) -> bool:
    return _mime(o).startswith(("image/", "application/pdf"))


def _hash(got: dict) -> int | None:
    """The dHash a status carries, or None when it has none or a malformed one (a hand-edited dump)."""
    h = got.get("dhash")
    if not isinstance(h, str) or len(h) != HASH_SIDE * HASH_SIDE // 4:
        return None
    try:
        return int(h, 16)
    except ValueError:
        return None


def tone_share(hist: list[int]) -> float:
    """Share of pixels within TONE_WINDOW grey levels of the most common one. `hist` is a 256-bin histogram."""
    total = sum(hist)
    if not total:
        return 0.0
    top = max(range(len(hist)), key=lambda i: hist[i])
    return sum(hist[max(0, top - TONE_WINDOW):top + TONE_WINDOW + 1]) / total


def dhash(rows: list[list[int]]) -> int:
    """Difference hash: one bit per pair of neighbours in a row, set when the left one is brighter."""
    bits = 0
    for row in rows:
        for a, b in zip(row, row[1:]):
            bits = bits << 1 | (a > b)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def measure(data: bytes) -> dict:
    """Size, tone share and dHash of a thumbnail. Needs Pillow; raises what Pillow raises on a file it cannot read."""
    from PIL import Image

    with Image.open(BytesIO(data)) as img:
        img.load()
        w, h = img.size
        grey = img.convert("L")
        small = grey.resize((HASH_SIDE + 1, HASH_SIDE))
        px = list(small.tobytes())
        rows = [px[i:i + HASH_SIDE + 1] for i in range(0, len(px), HASH_SIDE + 1)]
        return {"w": w, "h": h, "tone": round(tone_share(grey.histogram()), 4),
                "dhash": f"{dhash(rows):0{HASH_SIDE * HASH_SIDE // 4}x}"}


# a thumbnail the server will not make: 404 gone, 413 too large, 415 no thumbnailer, 422 a file it cannot read
# (corrupt, HEIC without its plugin), 501 a PDF without poppler; counted as unread, never the end of the run
NO_THUMB = frozenset(range(400, 500)) - {401, 429} | {501}


def annotate(client, objects: dict, status: dict | None = None, *, download: bool = False,
             workers: int = 4) -> dict:
    """What the server says about each media object, {handle: {...}}: whether its file is missing (one request,
    GET /api/media/?filemissing=1), and with `download` the measures of its thumbnail. Measures in `status`, from
    an earlier dump, stay until measured again."""
    media = objects.get("media") or []
    media = list(media.values()) if isinstance(media, dict) else media
    # Gramps Web caches a list answer until the tree changes, and deleting a file changes nothing in the tree: a
    # pagesize it ignores without `page`, new on every call, keeps a cached answer from before the deletion out
    try:
        _, rows = client.get("/media/", {"filemissing": 1, "keys": "handle", "pagesize": random.randrange(1, 2**62)})
        missing = {r["handle"] for r in rows}
    except ApiError as exc:  # media storage that fails (an S3 list, a slow share) must not end a tree check
        print(f"gwt-check: media files not checked, the server's list of missing files failed: {exc}", file=sys.stderr)
        missing = None
    old = status or {}
    status = {}
    for o in media:
        got = status[o["handle"]] = {k: v for k, v in (old.get(o["handle"]) or {}).items() if k != "file_missing"}
        if missing is not None:
            got["file_missing"] = o["handle"] in missing
    if not download:
        return status
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("gwt-check: --download needs Pillow (pipx inject gramps-web-toolkit pillow, or pip install pillow "
              "beside gwt-check); media-tiny, media-uniform and media-dup-page skipped", file=sys.stderr)
        return status
    todo = [o for o in media if is_image(o) and not status[o["handle"]].get("file_missing")]
    def one(o):
        got = status[o["handle"]]
        _, data = client.get(f"/media/{o['handle']}/thumbnail/{THUMB}", {}, raw=True, none_on=NO_THUMB)
        try:
            if data is None:
                raise ValueError("no thumbnail")
            got.update(measure(data))
        except Exception:  # noqa: BLE001 - Pillow raises many kinds on a file it cannot read
            for k in ("w", "h", "tone", "dhash"):  # an earlier dump's measures are of what may be another file now
                got.pop(k, None)
            got["unread"] = True
            return False
        got.pop("unread", None)
        return True

    with ThreadPoolExecutor(max(1, workers)) as pool:
        unread = sum(not ok for ok in pool.map(one, todo))
    print(f"gwt-check: {len(todo) - unread} thumbnail(s) measured" + (
        f", {unread} the server would not give or Pillow could not read (an AVIF thumbnail needs Pillow 11.3 "
        "or newer)" if unread else ""), file=sys.stderr)
    return status


def media_checks(T: dict, status: dict, out, in_scope, info: dict, label) -> None:
    """The media kinds, on the tree's media objects and what the server said about them (`status`). Single-object
    kinds report media in scope; a same-page pair needs one side in scope."""
    M = T["media"]
    S = {h: status.get(h) if isinstance(status.get(h), dict) else {} for h in M}
    unchecked = unread = 0
    for h, o in M.items():
        got = S[h]
        if "file_missing" not in got:
            unchecked += 1
        unread += bool(got.get("unread"))
        if not in_scope("media", h):
            continue
        gid = o.get("gramps_id") or h
        mime = non_viewable(o)
        if mime:
            fix = ("put its text in a Transcript note (gwt-apply add_transcription), then detach it"
                   if mime.startswith(NON_VIEWABLE_PREFIXES) else
                   "unpack or convert it to an image or PDF, or detach it")
            out.add("media-non-viewable", gid, f"{mime}: shown as an icon; {fix}")
        if got.get("file_missing"):
            out.add("media-file-missing", gid, f"no file on the server for {o.get('path') or 'its path'}: "
                                               "upload it again, or detach it")
        if isinstance(got.get("w"), int) and isinstance(got.get("h"), int) and max(got["w"], got["h"]) < MIN_SIDE:
            out.add("media-tiny", gid, f"{got['w']}x{got['h']} px: replace it with the full image, or detach it")
        if isinstance(got.get("tone"), (int, float)) and got["tone"] >= UNIFORM_SHARE:
            out.add("media-uniform", gid, f"{got['tone']:.0%} of it is one tone: a blank, grey or failed image; "
                                          "replace it, or detach it")
    if unchecked:
        info["media files not checked"] = unchecked
    if unread:  # asked with --download, and the server would not give it or Pillow could not read it
        info["media thumbnails not read"] = unread
    # the same page twice on one citation: near thumbnails, different files (the same file is dup-media)
    for c in T["citations"].values():
        refs = [r.get("ref") for r in c.get("media_list") or [] if isinstance(r, dict)]
        hashed = [(h, _hash(S[h])) for h in dict.fromkeys(refs) if h in M and _hash(S[h]) is not None]
        for i, (a, ha) in enumerate(hashed):
            for b, hb in hashed[i + 1:]:
                if M[a].get("checksum") and M[a].get("checksum") == M[b].get("checksum"):
                    continue
                if not (in_scope("media", a) or in_scope("media", b)):
                    continue
                bits = hamming(ha, hb)
                if bits <= DUP_BITS:
                    pair = ",".join(sorted((label("media", a), label("media", b)), key=_id_order))
                    out.add("media-dup-page", f"{c.get('gramps_id') or c['handle']} {pair}",
                            f"{bits} of {HASH_SIDE * HASH_SIDE} bits apart: may be the same page twice, though pages "
                            "of one printed form can look alike; compare them before detaching one")


def _id_order(gid: str):
    from .checks import id_order
    return id_order(gid)


def print_media_info(info: dict) -> None:
    if info.get("media files not checked"):
        print(f"media files not checked: {info['media files not checked']} (whether a file is missing is read from "
              "the live tree; a dump gwt-check saved carries it)")
    if info.get("media thumbnails not read"):
        print(f"media thumbnails not read: {info['media thumbnails not read']} (the server would not give them or "
              "Pillow could not read them; media-tiny, media-uniform and media-dup-page did not see them)")
