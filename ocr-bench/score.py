#!/usr/bin/env python3
"""Score OCR/HTR engine outputs against reference transcriptions.

    python3 score.py --refs REFS --outputs OUT [--tsv per-page.tsv] [--md summary.md]

REFS holds one <page id>.txt per page: a few `# key: value` header lines, then the text.
    # image: path/to/page.jpg      (used by run.sh, ignored here)
    # type: parish-1700-1800       (pages are grouped by this in the tables)
    # scope: full | partial, ...   (partial = the reference covers only part of the page)
    # names: Pedro Ejemplo; Ana Muestra; Villanueva
    <reference text>

OUT holds one folder per engine with <page id>.txt files and, optionally, the timing.tsv
the adapters write. Any folder counts as an engine, so a transcription made by hand, by
FamilySearch's own HTR or by an LLM is scored the same way: drop the files in a folder.

What is measured, per page and engine:
- CER and WER: edit distance over the reference length, after normalising both sides
  (lower case, accents and punctuation removed, `[editorial insertions]` dropped).
  The reference is split at every `…` into segments, and each segment is matched against
  the best-fitting stretch of the engine output (infix alignment), so a reference that
  covers one entry of a full page is not charged for the rest of the page, nor for the
  order in which the engine walked the columns.
- names: share of the `# names:` list found in the output, each name allowed one edit per
  six letters (so «Marz Garcia» still matches «Mrz Garcia», but «Pujol» must be exact).
"""
import argparse
import os
import re
import statistics
import sys
import unicodedata


SPLIT = re.compile(r"…|\.\.\.")


def normalise(text):
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def read_ref(path):
    meta, body = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"#\s*(\w+):\s*(.*)", line)
            if m and not body:
                meta[m.group(1)] = m.group(2).strip()
            else:
                body.append(line)
    meta["text"] = "".join(body).strip()
    meta["names"] = [n.strip() for n in meta.get("names", "").split(";") if n.strip()]
    return meta


def infix_distance(query, target):
    if not query:
        return 0
    if not target:
        return len(query)
    import edlib  # pip install edlib; imported here so stage.py runs without it
    return edlib.align(query, target, mode="HW", task="distance")["editDistance"]


def infix_words(query, target):
    """infix_distance over word lists. edlib takes at most 256 distinct symbols, so every
    output word that is not in the query becomes one shared symbol (it can never match)."""
    vocab = {w: chr(0x100 + i) for i, w in enumerate(dict.fromkeys(query))}
    if len(vocab) < 255:
        other = chr(0x100 + 255)
        return infix_distance("".join(vocab[w] for w in query), "".join(vocab.get(w, other) for w in target))
    prev = [0] * (len(target) + 1)  # plain dynamic programming, free start and end in target
    for i, q in enumerate(query, 1):
        cur = [i] + [0] * len(target)
        for j, t in enumerate(target, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (q != t))
        prev = cur
    return min(prev)


def score_page(ref, hyp_raw):
    segs = [normalise(s) for s in SPLIT.split(ref["text"])]
    segs = [s for s in segs if s]
    hyp = normalise(hyp_raw)
    c_err = sum(infix_distance(s, hyp) for s in segs)
    c_len = sum(len(s) for s in segs)
    w_hyp, w_segs = hyp.split(), [s.split() for s in segs]
    w_err = sum(infix_words(s, w_hyp) for s in w_segs)
    w_len = sum(len(s) for s in w_segs)
    found = 0
    for name in ref["names"]:
        n = normalise(name)
        if n and infix_distance(n, hyp) <= len(n.replace(" ", "")) // 6:
            found += 1
    return {
        "cer": c_err / c_len if c_len else 0.0,
        "wer": w_err / w_len if w_len else 0.0,
        "names_found": found,
        "names_total": len(ref["names"]),
        "chars": c_len,
        "words": w_len,
    }


def read_timing(path):
    secs, load = {}, []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            k, _, v = line.rstrip("\n").partition("\t")
            if k == "#load":
                load.append(float(v))
            elif v and v != "ERROR":
                secs[k] = float(v)
    return secs, load


def spot_score(word, hyp):
    """1.0 = the word appears exactly in the output; 0.5 = half its letters need an edit."""
    return 1 - infix_distance(word, hyp) / len(word)


def spot_table(refs, engines, outputs, out, thresholds=(0.65, 0.75, 0.8, 0.85, 0.9, 1.0)):
    """Triage test: every name word of 5+ letters from any reference is searched in every
    page's output. It is a hit when it is in that page's reference text, otherwise a miss.
    For each threshold X: share of hits found (recall) and share of misses wrongly flagged."""
    words = sorted({w for r in refs.values() for n in r["names"] for w in normalise(n).split() if len(w) >= 5})
    out.write("\n### surname spotting: found on the page vs. flagged on a page without it\n\n")
    out.write("| engine | pages | " + " | ".join(f"score >= {t}" for t in thresholds) + " |\n")
    out.write("|---|---:|" + "---:|" * len(thresholds) + "\n")
    for eng in engines:
        pos, neg = [], []
        for pid, ref in refs.items():
            p = os.path.join(outputs, eng, pid + ".txt")
            if not os.path.exists(p):
                continue
            hyp = normalise(open(p, encoding="utf-8").read())
            truth = set(normalise(ref["text"]).split())
            for w in words:
                (pos if w in truth else neg).append(spot_score(w, hyp))
        if not pos:
            continue
        npages = sum(os.path.exists(os.path.join(outputs, eng, pid + ".txt")) for pid in refs)
        cells = [f"{100 * sum(x >= t for x in pos) / len(pos):.0f}% / {100 * sum(x >= t for x in neg) / len(neg):.1f}%"
                 for t in thresholds]
        out.write(f"| {eng} | {npages} | " + " | ".join(cells) + " |\n")
    out.write("\nEach cell: share of surnames that are on the page and score at least X / share of surnames "
              f"that are not on the page but still score at least X. {len(words)} name words tested on every page. "
              "«On the page» means in the reference text, so with partial references a surname written elsewhere "
              "on the page counts against the engine: the second number is then an upper bound.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--outputs", required=True)
    ap.add_argument("--tsv", help="write one row per page and engine here")
    ap.add_argument("--md", help="write the summary tables here (default: stdout)")
    ap.add_argument("--spot", action="store_true",
                    help="add the surname-spotting table: is a surname on the page, judged from the OCR alone?")
    ap.add_argument("--engines", help="comma-separated engine folders to score, in this order")
    args = ap.parse_args()

    refs = {os.path.splitext(f)[0]: read_ref(os.path.join(args.refs, f))
            for f in sorted(os.listdir(args.refs)) if f.endswith(".txt")}
    engines = args.engines.split(",") if args.engines else sorted(
        d for d in os.listdir(args.outputs) if os.path.isdir(os.path.join(args.outputs, d)))

    rows = []
    for eng in engines:
        secs, _ = read_timing(os.path.join(args.outputs, eng, "timing.tsv"))
        for pid, ref in refs.items():
            p = os.path.join(args.outputs, eng, pid + ".txt")
            if not os.path.exists(p):
                continue
            r = score_page(ref, open(p, encoding="utf-8").read())
            r.update(engine=eng, page=pid, type=ref.get("type", "?"), secs=secs.get(pid))
            rows.append(r)

    if args.tsv:
        cols = ["engine", "page", "type", "cer", "wer", "names_found", "names_total", "chars", "words", "secs"]
        with open(args.tsv, "w", encoding="utf-8") as f:
            f.write("\t".join(cols) + "\n")
            for r in rows:
                f.write("\t".join("" if r[c] is None else (f"{r[c]:.4f}" if isinstance(r[c], float) else str(r[c]))
                                  for c in cols) + "\n")

    out = open(args.md, "w", encoding="utf-8") if args.md else sys.stdout
    types = sorted({r["type"] for r in rows})
    for group in ["all"] + types:
        sel = [r for r in rows if group == "all" or r["type"] == group]
        npages = len({r["page"] for r in sel})
        out.write(f"\n### {group} ({npages} pages)\n\n")
        out.write("| engine | pages | CER | WER | names found | s/page |\n|---|---:|---:|---:|---:|---:|\n")
        stats = []
        for eng in engines:
            er = [r for r in sel if r["engine"] == eng]
            if not er:
                continue
            chars = sum(r["chars"] for r in er)
            cer = sum(r["cer"] * r["chars"] for r in er) / chars
            wer = sum(r["wer"] * r["words"] for r in er) / max(1, sum(r["words"] for r in er))
            nt = sum(r["names_total"] for r in er)
            nf = sum(r["names_found"] for r in er)
            secs = [r["secs"] for r in er if r["secs"] is not None]
            stats.append((cer, eng, len(er), wer, nf, nt, statistics.mean(secs) if secs else None))
        for cer, eng, n, wer, nf, nt, s in sorted(stats):
            names = f"{100 * nf / nt:.0f}% ({nf}/{nt})" if nt else "-"
            out.write(f"| {eng} | {n} | {100 * cer:.1f}% | {100 * wer:.1f}% | {names} | {'' if s is None else f'{s:.1f}'} |\n")
    if args.spot:
        spot_table(refs, engines, args.outputs, out)
    if args.md:
        out.close()


if __name__ == "__main__":
    main()
