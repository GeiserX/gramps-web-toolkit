#!/usr/bin/env python3
"""Copy each reference's page image to PAGES/<page id>.<ext>, the layout run.sh expects.

    python3 stage.py --refs REFS --root REPO_ROOT --out PAGES

`# image:` paths in the references are taken relative to --root. Copy PAGES to the box
that runs the engines afterwards (rsync); page ids keep private file names off that box.
"""
import argparse
import os
import shutil

from score import read_ref

ap = argparse.ArgumentParser()
ap.add_argument("--refs", required=True)
ap.add_argument("--root", default=".")
ap.add_argument("--out", required=True)
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
for f in sorted(os.listdir(args.refs)):
    if not f.endswith(".txt"):
        continue
    pid, img = f[:-4], read_ref(os.path.join(args.refs, f)).get("image")
    if not img:
        continue
    dst = os.path.join(args.out, pid + os.path.splitext(img)[1].lower())
    if not os.path.exists(dst):
        shutil.copyfile(os.path.join(args.root, img), dst)
    print(dst)
