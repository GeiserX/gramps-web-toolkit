"""The output folder: files written atomically, never overwritten, and one manifest.tsv row per file.

A run that dies leaves only `*.part` files behind, so any finished file on disk is complete.
Re-running the same command skips what is already there and adds a row for any finished file
the manifest is missing (the run died between the rename and the append). A file that gwt-rawstore
moved to the bucket (a row in the folder's rawstore.tsv) counts as there, so it is not fetched again.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

RAWSTORE = "rawstore.tsv"  # gwt-rawstore's manifest: a row is a file uploaded and confirmed by the bucket
COLUMNS = ["file", "label", "image_url", "manifest_url", "sha256", "fetched_at"]
EXT = {
    "image/jpeg": ".jpg", "image/jp2": ".jp2", "image/png": ".png", "image/tiff": ".tif",
    "image/gif": ".gif", "image/webp": ".webp", "application/pdf": ".pdf", "text/plain": ".txt",
}


def slug(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:limit].strip("-") or "x"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, out: str | os.PathLike):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.manifest = self.out / "manifest.tsv"
        self.rows: dict[str, dict] = {}
        if self.manifest.exists():
            with open(self.manifest, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter="\t"):
                    self.rows[row["file"]] = row
        self.stored = _rawstore_names(self.out / RAWSTORE)

    def find(self, stem: str) -> Path | None:
        """The finished file for this stem, whatever its extension. It may live only in the bucket:
        then the path does not exist on disk."""
        for p in sorted(self.out.glob(stem + ".*")):
            if p.suffix not in (".part", ".tsv"):
                return p
        for name in sorted(self.stored):
            if name.startswith(stem + "."):
                return self.out / name
        return None

    def done(self, stem: str, label: str, image_url: str, manifest_url: str) -> Path | None:
        """If a finished file exists (on disk or in the bucket), make sure it has a row and return it."""
        p = self.find(stem)
        if p and p.name not in self.rows and p.exists():
            self.record(p, label, image_url, manifest_url)
        return p

    def write(self, stem: str, chunks, content_type: str, label: str, image_url: str, manifest_url: str) -> Path:
        ext = EXT.get((content_type or "").split(";")[0].strip().lower(), ".bin")
        final = self.out / (stem + ext)
        if final.exists():  # never overwrite
            return final
        part = final.with_suffix(final.suffix + ".part")
        with open(part, "wb") as f:
            for c in chunks:
                f.write(c)
        os.replace(part, final)
        self.record(final, label, image_url, manifest_url)
        return final

    def record(self, path: Path, label: str, image_url: str, manifest_url: str) -> None:
        row = {"file": path.name, "label": _clean(label), "image_url": image_url,
               "manifest_url": manifest_url, "sha256": sha256_file(path), "fetched_at": now()}
        new = not self.manifest.exists()
        with open(self.manifest, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, COLUMNS, delimiter="\t", lineterminator="\n")
            if new:
                w.writeheader()
            w.writerow(row)
        self.rows[path.name] = row


def _rawstore_names(path: Path) -> set[str]:
    """File names listed in a gwt-rawstore manifest (columns sha256, size, key, path)."""
    if not path.is_file():
        return set()
    with open(path, encoding="utf-8") as f:
        rows = [line.rstrip("\n").split("\t") for line in f]
    return {r[3] for r in rows if len(r) == 4 and r[0] != "sha256" and "/" not in r[3]}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()
