"""gwt-fetch: pull archive images and text without a browser."""
from __future__ import annotations

import argparse
import sys

from . import digibib, europeana, gazeta, geo, iiif
from .net import Client


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

    s = sub.add_parser("iiif", help="download canvases from a IIIF manifest or a known viewer URL")
    s.add_argument("url")
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

    s = sub.add_parser("europeana", help="search Europeana (needs EUROPEANA_KEY); prints TSV with manifests")
    s.add_argument("query")
    s.add_argument("--provider", default="Hispana", help='aggregator, default Hispana; "any" for none')
    s.add_argument("--data-provider", help='institution, e.g. "Galiciana. Arquivo Dixital de Galicia"')
    s.add_argument("--country", help="e.g. spain")
    s.add_argument("--year-from")
    s.add_argument("--year-to")
    s.add_argument("--iiif-only", action="store_true", help="only records whose provider serves IIIF")
    s.add_argument("--rows", type=int, default=20)

    s = sub.add_parser("geo", help="geocode Spanish places through CartoCiudad; prints TSV with INE codes")
    s.add_argument("places", nargs="+")
    s.add_argument("--types", default="Municipio,poblacion",
                   help="CartoCiudad types to keep (Municipio, poblacion, toponimo, callejero); empty for all")
    s.add_argument("--province")
    s.add_argument("--limit", type=int, default=3, help="rows per place")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    client = Client(delay=args.delay)
    mod = {"iiif": iiif, "digibib": digibib, "gazeta": gazeta, "europeana": europeana, "geo": geo}[args.cmd]
    try:
        return mod.run(args, client)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # network and parse errors: one line, non-zero exit
        print(f"gwt-fetch: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
