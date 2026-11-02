"""Cut a saved image into a box, a grid of tiles or a half, beside the original, with a manifest.tsv row each.

Each piece is a JPEG named after the original and the cut (`0001_pag-1_box-10-20-300-400.jpg`). Its row
uses the same columns as a download: `label` names the cut, the source file and the source's sha256,
`image_url` is the source's URL (or file name) with `#xywh=x,y,w,h` in source pixels, `manifest_url`
is the source's. Never overwrites; re-running skips finished pieces. Needs Pillow.
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

from .store import COLUMNS, Store, sha256_file


def parse_box(text: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4 or not all(re.fullmatch(r"\d+", p) for p in parts):
        raise SystemExit(f"gwt-fetch crop: --box wants x,y,w,h in whole pixels, got {text!r}")
    x, y, w, h = map(int, parts)
    if not w or not h:
        raise SystemExit("gwt-fetch crop: --box width and height must be above 0")
    return x, y, w, h


def parse_pair(text: str, what: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", text or "")
    if not m or not int(m.group(1)) or not int(m.group(2)):
        raise SystemExit(f"gwt-fetch crop: {what} wants AxB, e.g. 2x3, got {text!r}")
    return int(m.group(1)), int(m.group(2))


def scale_box(box, from_size, size):
    """A box measured on an image of from_size, in the pixels of an image of size.

    The edges are scaled, not the width and height, so a box that ends at the preview's edge
    ends at the image's edge instead of rounding one pixel past it.
    """
    (x, y, w, h), (fw, fh), (W, H) = box, from_size, size
    sx, sy = W / fw, H / fh
    x0, y0, x1, y1 = round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy)
    return x0, y0, x1 - x0, y1 - y0


def grid(size, cols: int, rows: int, overlap: float):
    """(name, box) per tile, row by row; each tile reaches `overlap` of its size into its neighbours."""
    W, H = size
    out = []
    for r in range(rows):
        for c in range(cols):
            x0, x1 = W * c // cols, W * (c + 1) // cols
            y0, y1 = H * r // rows, H * (r + 1) // rows
            ox, oy = round((x1 - x0) * overlap), round((y1 - y0) * overlap)
            x0, x1 = max(0, x0 - ox), min(W, x1 + ox)
            y0, y1 = max(0, y0 - oy), min(H, y1 + oy)
            out.append((f"grid{cols}x{rows}-r{r + 1}c{c + 1}", (x0, y0, x1 - x0, y1 - y0)))
    return out


def half(size, side: str):
    W, H = size
    mid = W // 2
    return (side, (0, 0, mid, H) if side == "left" else (mid, 0, W - mid, H))


def parse_overlap(text: str) -> float:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%?\s*", text or "")
    if not m or float(m.group(1)) >= 50:
        raise SystemExit(f"gwt-fetch crop: --overlap wants a percentage under 50, e.g. 10%, got {text!r}")
    return float(m.group(1)) / 100


def pieces(args, size):
    """(name, box in source pixels) for every piece the flags ask for."""
    W, H = size
    if args.from_size and not args.box:
        raise SystemExit("gwt-fetch crop: --from-size goes with --box")
    if parse_overlap(args.overlap) and not args.grid:
        raise SystemExit("gwt-fetch crop: --overlap goes with --grid")
    if args.box:
        box = parse_box(args.box)
        if args.from_size:
            box = scale_box(box, parse_pair(args.from_size, "--from-size"), size)
        x, y, w, h = box
        if x + w > W or y + h > H:
            raise SystemExit(f"gwt-fetch crop: box {x},{y},{w},{h} reaches past the {W}x{H} image")
        return [(f"box-{x}-{y}-{w}-{h}", box)]
    if args.grid:
        cols, rows = parse_pair(args.grid, "--grid")
        return grid(size, cols, rows, parse_overlap(args.overlap))
    return [half(size, args.half)]


def in_root(parent_url: str, box, size) -> str:
    """The URL of box (in a piece of `size` pixels) as one #xywh= fragment on the root image.

    A piece's own row ends in #xywh=X,Y,W,H in the root's pixels; the box is scaled by W,H over
    the piece's real size (it differs after --max) and moved by X,Y.
    """
    x, y, w, h = box
    m = re.search(r"#xywh=(\d+),(\d+),(\d+),(\d+)$", parent_url)
    if not m:
        return f"{parent_url}#xywh={x},{y},{w},{h}"
    X, Y, PW, PH = map(int, m.groups())
    x0, y0, w, h = scale_box(box, size, (PW, PH))
    return f"{parent_url[:m.start()]}#xywh={X + x0},{Y + y0},{w},{h}"


def _ours(folder: Path) -> bool:
    """True when folder has no manifest.tsv, or one with gwt-fetch's columns."""
    m = folder / "manifest.tsv"
    if not m.exists():
        return True
    with open(m, encoding="utf-8") as f:
        return f.readline().rstrip("\r\n").split("\t") == COLUMNS


def run(args, client=None) -> int:
    try:
        from PIL import Image
    except ImportError:
        print("gwt-fetch crop needs Pillow: pip install 'gramps-web-toolkit[crop]' (or pip install Pillow)",
              file=sys.stderr)
        return 2
    for name in args.images:
        src = Path(name)
        if not src.is_file():
            raise SystemExit(f"gwt-fetch crop: no such file {src}")
        out = Path(args.out) if getattr(args, "out", None) else src.parent
        if not _ours(out):
            raise SystemExit(f"gwt-fetch crop: {out / 'manifest.tsv'} has other columns than gwt-fetch's; "
                             f"write the pieces to another folder with --out DIR")
        row = Store(src.parent).rows.get(src.name, {}) if _ours(src.parent) else {}
        store = Store(out)
        here = src.name if out.resolve() == src.parent.resolve() else os.path.relpath(src, out)
        src_sha = sha256_file(src)
        with Image.open(src) as im:
            im.load()
            for cut, (x, y, w, h) in pieces(args, im.size):
                stem = f"{src.stem}_{cut}"
                label = f"crop {x},{y},{w},{h} of {src.name} sha256:{src_sha}"
                url = in_root(row.get("image_url") or here, (x, y, w, h), im.size)
                if store.done(stem, label, url, row.get("manifest_url", "")):
                    continue
                piece = im.crop((x, y, x + w, y + h))
                if args.max and max(piece.size) > args.max:
                    piece.thumbnail((args.max, args.max), Image.LANCZOS)
                if piece.mode not in ("RGB", "L"):
                    piece = piece.convert("RGB")
                buf = io.BytesIO()
                piece.save(buf, "JPEG", quality=92)
                p = store.write(stem, [buf.getvalue()], "image/jpeg", label, url, row.get("manifest_url", ""))
                print(p)
    return 0
