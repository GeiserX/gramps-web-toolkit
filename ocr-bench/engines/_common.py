"""Shared loop for the engine adapters. Standard library only: every engine has its own venv.

An adapter loads its model once, then reads each page image and writes `<out>/<page id>.txt`,
where the page id is the image file name without its extension. Seconds per page go to
`<out>/timing.tsv`, so model loading is not counted against any one page.
"""
import argparse
import os
import sys
import time


def run(read_page, load=None, extra_args=()):
    """read_page(model, image_path, args) -> text. load(args) -> model, called once."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="folder for <page id>.txt and timing.tsv")
    ap.add_argument("--force", action="store_true", help="redo pages that already have output")
    for flags, kw in extra_args:
        ap.add_argument(*flags, **kw)
    ap.add_argument("images", nargs="+")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    todo = [p for p in args.images
            if args.force or not os.path.exists(os.path.join(args.out, page_id(p) + ".txt"))]
    if not todo:
        return
    t0 = time.monotonic()
    model = load(args) if load else None
    load_s = time.monotonic() - t0
    with open(os.path.join(args.out, "timing.tsv"), "a", encoding="utf-8") as timing:
        timing.write(f"#load\t{load_s:.2f}\n")
        for path in todo:
            t = time.monotonic()
            try:
                text = read_page(model, path, args)
            except Exception as e:  # one bad page must not stop the run
                print(f"{page_id(path)}: {type(e).__name__}: {e}", file=sys.stderr)
                timing.write(f"{page_id(path)}\tERROR\n")
                continue
            secs = time.monotonic() - t
            with open(os.path.join(args.out, page_id(path) + ".txt"), "w", encoding="utf-8") as f:
                f.write(text.strip() + "\n")
            timing.write(f"{page_id(path)}\t{secs:.2f}\n")
            timing.flush()
            print(f"{page_id(path)}\t{secs:.1f}s\t{len(text)} chars", file=sys.stderr)


def page_id(path):
    return os.path.splitext(os.path.basename(path))[0]


def columns(items):
    """Split boxes into columns at vertical gutters, left to right.

    A gutter is a strip, in the middle 70% of the page, that almost no narrow box crosses.
    Two-page spreads split into their two pages the same way.
    """
    width = max(b[2] for b in items)
    narrow = [b for b in items if b[2] - b[0] < 0.45 * width]
    if len(narrow) < 10:
        return [items]
    bins = 200
    cov = [0] * bins
    for b in narrow:
        for i in range(int(b[0] / width * bins), min(bins, int(b[2] / width * bins) + 1)):
            cov[i] += 1
    thr, cuts, i = max(cov) * 0.03, [float("-inf")], int(bins * 0.15)
    while i < bins * 0.85:
        if cov[i] <= thr:
            j = i
            while j < bins * 0.85 and cov[j] <= thr:
                j += 1
            if j - i >= 2:
                cuts.append((i + j) / 2 / bins * width)
            i = j
        else:
            i += 1
    cuts.append(float("inf"))
    cols = [[b for b in items if cuts[k] <= (b[0] + b[2]) / 2 < cuts[k + 1]] for k in range(len(cuts) - 1)]
    return [c for c in cols if c]


def lines_in_reading_order(items):
    """items: (x0, y0, x1, y1, text) boxes -> text, column by column, top to bottom.

    Within a column, boxes whose vertical centres are within half a line height go on the
    same line, left to right.
    """
    if not items:
        return ""
    out = []
    for col in columns(items):
        col = sorted(col, key=lambda b: ((b[1] + b[3]) / 2, b[0]))
        lines, cur, cur_y, cur_h = [], [], None, None
        for b in col:
            y, h = (b[1] + b[3]) / 2, max(1e-6, b[3] - b[1])
            if cur and abs(y - cur_y) > 0.5 * max(h, cur_h):
                lines.append(cur)
                cur = []
            if not cur:
                cur_y, cur_h = y, h
            cur.append(b)
        if cur:
            lines.append(cur)
        out += [" ".join(t for *_, t in sorted(line, key=lambda b: b[0])) for line in lines]
    return "\n".join(out)
