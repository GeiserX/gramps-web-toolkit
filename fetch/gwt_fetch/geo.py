"""Geocode a Spanish place through CartoCiudad (IGN/CNIG): coordinates plus the INE municipality code."""
from __future__ import annotations

import sys
import unicodedata

API = "https://www.cartociudad.es/geocoder/api/geocoder"
COLUMNS = ["query", "type", "name", "municipality", "ine_code", "province", "lat", "lng", "cartociudad_id"]


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def geocode(client, query: str, types: list[str], province: str | None = None, limit: int = 5) -> list[dict]:
    cands = client.json(f"{API}/candidates", params={"q": query, "limit": 30})
    want = {t.lower() for t in types}
    out = []
    for c in cands:
        if want and c.get("type", "").lower() not in want:
            continue
        if province and _fold(province) not in _fold(c.get("province", "")):
            continue
        f = client.json(f"{API}/find", params={"q": c.get("address") or query, "id": c["id"], "type": c["type"]})
        out.append({
            "query": query,
            "type": c.get("type", ""),
            "name": (c.get("poblacion") if c.get("type") == "poblacion" else c.get("address")) or c.get("muni") or "",
            "municipality": c.get("muni") or "",
            "ine_code": c.get("muniCode") or "",
            "province": c.get("province") or "",
            "lat": f"{f['lat']:.6f}" if f.get("lat") else "",
            "lng": f"{f['lng']:.6f}" if f.get("lng") else "",
            "cartociudad_id": c["id"],
        })
        if len(out) >= limit:
            break
    return out


def run(args, client) -> int:
    print("\t".join(COLUMNS))
    n = 0
    for q in args.places:
        rows = geocode(client, q, [t for t in args.types.split(",") if t], args.province, args.limit)
        if not rows:
            print(f"gwt-fetch: no {args.types} match for {q!r}", file=sys.stderr)
        for r in rows:
            print("\t".join(str(r[c]) for c in COLUMNS))
        n += len(rows)
    return 0 if n else 1
