"""Benchmark gwt-apply on a throwaway Gramps Web server with an invented tree.

    python3 bench/synth.py seed  --url http://host:5000 --user owner --password ... [--people 13000]
    python3 bench/synth.py ops   --url ... --prefix a --count 5000 > ops-a.jsonl

`seed` fills an empty tree with invented people, families, events, sources, citations,
notes and media records (no files) through POST /api/objects/, in batches. `ops` writes a
change list that looks like a research round: new people with a baptism, a citation and a
note, linked as children of existing families, plus attributes and event corrections on
people already in the tree. Never point this at a real tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys

import requests

FIRST = ["Alba", "Bruno", "Cira", "Dario", "Elva", "Fermin", "Gala", "Hugo", "Ines", "Jacobo",
         "Lidia", "Mauro", "Nerea", "Olmo", "Paula", "Quirico", "Rita", "Saul", "Tea", "Ulises"]
SUR = ["Abeto", "Brezo", "Cedro", "Durillo", "Enebro", "Fresno", "Grama", "Haya", "Iris", "Jara",
       "Laurel", "Mirto", "Nogal", "Olivo", "Pino", "Roble", "Sauce", "Tejo", "Urce", "Vid"]


def token(s, url, user, password):
    r = s.post(url + "/api/token/", json={"username": user, "password": password}, timeout=60)
    r.raise_for_status()
    s.headers["Authorization"] = "Bearer " + r.json()["access_token"]


def h(*parts) -> str:
    return "syn" + hashlib.sha256("/".join(map(str, parts)).encode()).hexdigest()[:24]


def date(y, m=0, d=0):
    return {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
            "dateval": [d, m, y, False], "text": "", "sortval": 0, "newyear": 0}


def seed(args):
    rnd = random.Random(1)
    s = requests.Session()
    token(s, args.url, args.user, args.password)
    objs: list[dict] = []
    for i in range(args.sources):
        objs.append({"_class": "Source", "handle": h("S", i), "title": f"Invented register {i}, book {i % 9}"})
    for i in range(args.places):
        objs.append({"_class": "Place", "handle": h("P", i), "name": {"_class": "PlaceName", "value": f"Villa {i}"},
                     "place_type": "Parish"})
    for i in range(args.people):
        refs, cits, notes = [], [], []
        if rnd.random() < 0.75:
            ev = h("E", i)
            objs.append({"_class": "Event", "handle": ev, "type": "Birth", "date": date(1700 + i % 250, 1 + i % 12, 1 + i % 28),
                         "place": h("P", i % args.places)})
            refs.append({"_class": "EventRef", "ref": ev, "role": "Primary"})
        if i < args.citations:
            c = h("C", i)
            objs.append({"_class": "Citation", "handle": c, "source_handle": h("S", i % args.sources),
                         "page": f"folio {i}", "confidence": 2})
            cits.append(c)
            if i < args.media:
                m = h("M", i)
                objs.append({"_class": "Media", "handle": m, "path": f"invented/{i}.jpg", "mime": "image/jpeg",
                             "checksum": hashlib.md5(str(i).encode()).hexdigest()})
                objs[-2]["media_list"] = [{"_class": "MediaRef", "ref": m}]
        if i < args.notes:
            n = h("N", i)
            objs.append({"_class": "Note", "handle": n, "type": "Research",
                         "text": {"_class": "StyledText", "string": f"Invented note {i}", "tags": []}})
            notes.append(n)
        objs.append({"_class": "Person", "handle": h("I", i), "gender": i % 2,
                     "primary_name": {"_class": "Name", "first_name": FIRST[i % 20] + f" {i // 400}",
                                      "surname_list": [{"_class": "Surname", "surname": SUR[(i // 20) % 20], "primary": True}]},
                     "event_ref_list": refs, "birth_ref_index": 0 if refs else -1,
                     "citation_list": cits, "note_list": notes})
    for f in range(args.families):
        father, mother = 4 * f, 4 * f + 1
        kids = [4 * f + 2, 4 * f + 3]
        objs.append({"_class": "Family", "handle": h("F", f), "father_handle": h("I", father), "mother_handle": h("I", mother),
                     "type": "Married",
                     "child_ref_list": [{"_class": "ChildRef", "ref": h("I", k), "frel": "Birth", "mrel": "Birth"} for k in kids]})
    n = args.batch
    for start in range(args.skip, len(objs), n):
        r = s.post(args.url + "/api/objects/", json=objs[start:start + n], timeout=600)
        if r.status_code == 401:  # the access token lasts 15 minutes
            token(s, args.url, args.user, args.password)
            r = s.post(args.url + "/api/objects/", json=objs[start:start + n], timeout=600)
        if r.status_code != 201:
            sys.exit(f"seed batch {start}: HTTP {r.status_code} {r.text[:300]}")
        print(f"seeded {min(start + n, len(objs))}/{len(objs)}", file=sys.stderr)


def ids(s, url, ns):
    r = s.get(f"{url}/api/{ns}/", params={"keys": "handle,gramps_id"}, timeout=600)
    r.raise_for_status()
    return {row["handle"]: row["gramps_id"] for row in r.json()}


def make_ops(args):
    rnd = random.Random(args.prefix)
    s = requests.Session()
    token(s, args.url, args.user, args.password)
    people, fams = ids(s, args.url, "people"), ids(s, args.url, "families")
    sources, places, events = ids(s, args.url, "sources"), ids(s, args.url, "places"), ids(s, args.url, "events")
    src, plc, fam = sorted(sources.values()), sorted(places.values()), sorted(fams.values())
    per, ev = sorted(people.values()), sorted(events.values())
    out, unit, R = [], 0, f"bench-{args.prefix}"
    while len(out) < args.count:
        k = f"{args.prefix}{unit}"
        p, e, c = f"key:p-{k}", f"key:e-{k}", f"key:c-{k}"
        f = rnd.choice(fam)
        out += [
            {"op": "add_person", "key": f"p-{k}", "ruling": R, "first_name": f"Nuevo{k}", "surname": rnd.choice(SUR), "gender": rnd.choice("MF")},
            {"op": "add_event", "key": f"e-{k}", "ruling": R, "person": p, "type": "Baptism", "date": f"{1700 + unit % 250}-0{1 + unit % 9}-1{unit % 9}", "place": rnd.choice(plc)},
            {"op": "add_citation", "key": f"c-{k}", "ruling": R, "source": rnd.choice(src), "page": f"bench {k}", "confidence": 2, "attach": [{"event": e}, {"person": p}]},
            {"op": "link_child", "key": f"l-{k}", "ruling": R, "family": f, "child": p},
            {"op": "add_note", "key": f"n-{k}", "ruling": R, "type": "Research", "text": f"Invented research note {k}", "attach": [{"person": p}]},
            {"op": "set_attribute", "key": f"a-{k}", "ruling": R, "target": {"person": rnd.choice(per)}, "type": "Occupation", "value": f"trade {k}"},
            {"op": "update_event", "key": f"u-{k}", "ruling": R, "event": rnd.choice(ev), "set": {"description": f"checked {k}"}},
        ]
        unit += 1
    for op in out[: args.count]:
        print(json.dumps(op, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seed", "ops"])
    ap.add_argument("--url", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--people", type=int, default=13000)
    ap.add_argument("--families", type=int, default=3000)
    ap.add_argument("--sources", type=int, default=150)
    ap.add_argument("--places", type=int, default=250)
    ap.add_argument("--citations", type=int, default=6500)
    ap.add_argument("--media", type=int, default=5000)
    ap.add_argument("--notes", type=int, default=13000)
    ap.add_argument("--skip", type=int, default=0, help="objects already seeded by an interrupted run")
    ap.add_argument("--batch", type=int, default=100, help="objects per request; big family batches outlast the server's 120 s")
    ap.add_argument("--prefix", default="a")
    ap.add_argument("--count", type=int, default=5000)
    args = ap.parse_args()
    seed(args) if args.cmd == "seed" else make_ops(args)


if __name__ == "__main__":
    main()
