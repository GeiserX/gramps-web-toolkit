"""Europeana Search API, filtered to Spanish providers, returning a IIIF manifest per record.

Every Europeana record has a Europeana-built IIIF manifest. When the provider has no Image API,
that manifest points at Europeana's thumbnail proxy; `gwt-fetch iiif` swaps it for the original file.
"""
from __future__ import annotations

import os
import sys

API = "https://api.europeana.eu/record/v2/search.json"
COLUMNS = ["id", "title", "data_provider", "year", "type", "rights", "manifest", "is_shown_by", "is_shown_at"]


def _first(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v or "").replace("\t", " ").replace("\n", " ")


def params(key: str, query: str, provider: str | None, data_provider: str | None, country: str | None,
           year_from: str | None, year_to: str | None, iiif_only: bool, rows: int, start: int = 1) -> list:
    p = [("wskey", key), ("query", query), ("rows", str(min(rows, 100))), ("start", str(start)), ("profile", "standard")]
    if provider:
        p.append(("qf", f'PROVIDER:"{provider}"'))
    if data_provider:
        p.append(("qf", f'DATA_PROVIDER:"{data_provider}"'))
    if country:
        p.append(("qf", f"COUNTRY:{country.lower()}"))
    if year_from or year_to:
        p.append(("qf", f"YEAR:[{year_from or '*'} TO {year_to or '*'}]"))
    if iiif_only:
        p.append(("qf", "sv_dcterms_conformsTo:*iiif*"))
    return p


def row(item: dict) -> dict:
    rid = item.get("id", "")
    return {
        "id": rid,
        "title": _first(item.get("title")),
        "data_provider": _first(item.get("dataProvider")),
        "year": _first(item.get("year")),
        "type": _first(item.get("type")),
        "rights": _first(item.get("rights")),
        "manifest": f"https://iiif.europeana.eu/presentation{rid}/manifest" if rid else "",
        "is_shown_by": _first(item.get("edmIsShownBy")),
        "is_shown_at": _first(item.get("edmIsShownAt")),
    }


def run(args, client) -> int:
    key = os.environ.get("EUROPEANA_KEY", "").strip()
    if not key:
        print("gwt-fetch: EUROPEANA_KEY is not set; skipping Europeana. Get a free key at "
              "https://pro.europeana.eu/pages/get-api and export EUROPEANA_KEY=...", file=sys.stderr)
        return 0
    provider = None if args.provider.lower() in ("", "any") else args.provider
    out, start = [], 1
    while len(out) < args.rows:
        d = client.json(API, params=params(key, args.query, provider, args.data_provider, args.country,
                                           args.year_from, args.year_to, args.iiif_only, args.rows - len(out), start))
        if not d.get("success", True):
            raise SystemExit(f"gwt-fetch: Europeana says: {d.get('error')}")
        items = d.get("items") or []
        out += [row(i) for i in items]
        start += len(items)
        if not items or start > int(d.get("totalResults") or 0):
            break
    print("\t".join(COLUMNS))
    for r in out[: args.rows]:
        print("\t".join(r[c] for c in COLUMNS))
    print(f"gwt-fetch: {len(out[: args.rows])} record(s)", file=sys.stderr)
    return 0
