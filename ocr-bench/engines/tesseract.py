"""Tesseract 5 with the tessdata_best LSTM models. Built for print; included as the baseline."""
import os
import subprocess

from _common import run


def read(_, path, args):
    cmd = ["tesseract", path, "-", "-l", args.lang, "--oem", "1", "--psm", "3"]
    if args.tessdata:
        cmd += ["--tessdata-dir", args.tessdata]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


run(read, extra_args=[
    (["--lang"], {"default": "spa"}),
    (["--tessdata"], {"default": os.environ.get("TESSDATA_DIR")}),
])
