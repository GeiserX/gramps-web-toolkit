"""gwt-fetch: pull archive images and text without a browser."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import crop, digibib, europeana, gazeta, geo, iiif
from .net import Client
from .store import Store

RAWSTORE_HELP = "move each downloaded binary to the gwt-rawstore bucket once it confirms it, and delete the local copy"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gwt-fetch", description=__doc__)
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests to one host (default 1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def pick(sp, labels=True):
        sp.add_argument("--pages", help="1-based canvas positions, e.g. 1-5,9,12-")
        if labels:
            sp.add_argument("--labels", help="regular expression matched against canvas labels")
        sp.add_argument("--out", default=".", help="output folder; manifest.tsv is written there")
        sp.add_argument("--list", action="store_true", help="print what would be fetched, download nothing")
        sp.add_argument("--rawstore", action="store_true", help=RAWSTORE_HELP)

    s = sub.add_parser("iiif", help="download canvases from a IIIF manifest or a known viewer URL")
    s.add_argument("url", nargs="?", help="manifest URL or viewer URL (omit with --image-base)")
    s.add_argument("--image-base", metavar="URL",
                   help="a IIIF Image API base or its info.json, no manifest needed; {page} in it takes each --pages number")
    s.add_argument("--native", action="store_true",
                   help="fetch each canvas at the width its info.json gives, refusing a rescaled file")
    pick(s)

    s = sub.add_parser("digibib", help="download pages from a DIGIBIB viewer (BVPH, Defensa, ...)")
    s.add_argument("url", help=".../catalogo_imagenes/grupo.do?path=N")
    pick(s, labels=False)

    s = sub.add_parser("gazeta", help="search or fetch the historical Gaceta de Madrid (BOE Gazeta)")
    g = s.add_subparsers(dest="action", required=True)
    gs = g.add_parser("search", help="search; prints TSV")
    gs.add_argument("--text", help="words in the full text")
    gs.add_argument("--title", help="words in the title")
    gs.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    gs.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    gs.add_argument("--gazette", help="GAZ.ID of one title (7 = Gaceta de Madrid)")
    gs.add_argument("--limit", type=int, default=50)
    gf = g.add_parser("fetch", help="download the PDF and text of one or more references")
    gf.add_argument("refs", nargs="+", metavar="BOE-A-YYYY-N")
    gf.add_argument("--out", default=".")
    gf.add_argument("--rawstore", action="store_true", help=RAWSTORE_HELP)

    s = sub.add_parser("europeana", help="search Europeana (needs EUROPEANA_KEY); prints TSV with manifests")
    s.add_argument("query")
    s.add_argument("--provider", default="any", help='aggregator, e.g. Hispana for Spain; default any (no filter)')
    s.add_argument("--data-provider", help='institution, e.g. "Galiciana. Arquivo Dixital de Galicia"')
    s.add_argument("--country", help="e.g. spain")
    s.add_argument("--year-from")
    s.add_argument("--year-to")
    s.add_argument("--iiif-only", action="store_true", help="only records whose provider serves IIIF")
    s.add_argument("--rows", type=int, default=20)

    s = sub.add_parser("crop", help="cut a saved image into a box, a grid or a half (needs Pillow)")
    s.add_argument("images", nargs="+", metavar="IMG")
    how = s.add_mutually_exclusive_group(required=True)
    how.add_argument("--box", help="x,y,w,h in pixels")
    how.add_argument("--grid", help="columns x rows, e.g. 2x3")
    how.add_argument("--half", choices=["left", "right"])
    s.add_argument("--from-size", metavar="WxH",
                   help="the --box was measured on an image of this size (a preview); scale it to the real image")
    s.add_argument("--overlap", default="0%", help="with --grid, how far each tile reaches into its neighbours, e.g. 10%%")
    s.add_argument("--max", type=int, help="shrink each piece so its longest side is at most this many pixels")
    s.add_argument("--out", help="write pieces and their manifest.tsv rows here (default: beside each image)")

    s = sub.add_parser("geo", help="geocode Spanish places through CartoCiudad; prints TSV with INE codes")
    s.add_argument("places", nargs="+")
    s.add_argument("--types", default="Municipio,poblacion",
                   help="CartoCiudad types to keep (Municipio, poblacion, toponimo, callejero); empty for all")
    s.add_argument("--province")
    s.add_argument("--limit", type=int, default=3, help="rows per place")
    return p


def rawstore_module():
    """gwt_rawstore, installed beside gwt_fetch in the wheel, or the rawstore/ folder of a clone."""
    try:
        import gwt_rawstore
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rawstore"))
        import gwt_rawstore
    return gwt_rawstore


def to_bucket(out, bucket, prefix="", log=print) -> int:
    """Upload the binaries gwt-fetch wrote in out (rows of its manifest.tsv still on disk) and delete each one
    only after the bucket confirms it; its rawstore.tsv row then tells the next run it is done. Text stays.
    Returns the number of files not stored."""
    rs = rawstore_module()
    folder = Path(out)
    files = [str(folder / f) for f in sorted(Store(folder).rows) if rs.is_binary_name(f) and (folder / f).is_file()]
    return rs.put(bucket, files, prefix=prefix, remove=True, log=log) if files else 0


def main(argv=None, bucket=None) -> int:
    args = parser().parse_args(argv)
    client = Client(delay=args.delay)
    mod = {"iiif": iiif, "digibib": digibib, "gazeta": gazeta, "europeana": europeana, "geo": geo,
           "crop": crop}[args.cmd]
    rawstore = getattr(args, "rawstore", False) and not getattr(args, "list", False)
    if rawstore and bucket is None:  # a missing config stops the run before anything is downloaded
        rs = rawstore_module()
        cfg = rs.load_config()
        bucket, prefix = rs.store_from_config(cfg), cfg.get("GWT_RAWSTORE_PREFIX", "")
    else:
        prefix = ""
    try:
        rc = mod.run(args, client)
        if rawstore and to_bucket(args.out, bucket, prefix, log=print):
            rc = max(rc, 1)
        return rc
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # network and parse errors: one line, non-zero exit
        print(f"gwt-fetch: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
