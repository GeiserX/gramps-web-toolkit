"""Prove every check can fail: plant each error kind in a synthetic tree and look for it.

The tree is invented. Every planted error must come out as its finding, and the clean tree must
come out with none: a check that has never gone red proves nothing.
"""

from __future__ import annotations

import copy

from . import compare
from .tree import Tree


def _date(y=0, m=0, d=0, modifier=0):
    return {"_class": "Date", "calendar": 0, "dateval": [d, m, y, False], "modifier": modifier,
            "newyear": 0, "quality": 0, "sortval": 0, "text": "", "year": y}


def _base(gid: str, **kw) -> dict:
    o = {"handle": "h" + gid, "gramps_id": gid, "change": 1700000000, "private": False, "tag_list": []}
    o.update(kw)
    return o


def _ref(h: str, **kw) -> dict:
    return {"attribute_list": [], "citation_list": [], "note_list": [], "private": False, "ref": h, **kw}


def clean_tree() -> dict:
    """Three generations around I0001, with places, sources, citations, notes, media and a tag."""
    d: dict[str, list] = {t: [] for t in ("people", "families", "events", "places", "citations",
                                          "sources", "media", "notes", "repositories", "tags")}
    d["tags"].append(_base("", handle="htag1", name="run-1", color="#000000", priority=0))
    d["repositories"].append(_base("R0001", name="Archivo Diocesano de Ejemplo", type="Archive",
                                   address_list=[], urls=[], note_list=[]))
    d["places"] += [
        _base("P0001", name={"value": "Villaejemplo"}, place_type="Village", lat="41.5", long="-1.5",
              placeref_list=[{"ref": "hP0002", "date": _date()}], citation_list=[], media_list=[], note_list=[],
              alt_names=[], urls=[]),
        _base("P0002", name={"value": "Provincia Imaginaria"}, place_type="Province", lat="", long="",
              placeref_list=[], citation_list=[], media_list=[], note_list=[], alt_names=[], urls=[]),
    ]
    d["sources"].append(_base("S0001", title="Libro de bautismos de Villaejemplo", author="", pubinfo="",
                              abbrev="", attribute_list=[], media_list=[], note_list=[],
                              reporef_list=[{"ref": "hR0001", "call_number": "", "media_type": "Book",
                                             "note_list": [], "private": False}]))
    d["notes"] += [_base(f"N000{i}", type="General", format=0,
                         text={"_class": "StyledText", "string": f"Nota inventada {i}", "tags": []})
                   for i in (1, 2)]
    d["media"].append(_base("O0001", path="ejemplo/acta-0001.jpg", mime="image/jpeg", desc="Acta inventada",
                            checksum="0" * 32, date=_date(), attribute_list=[], citation_list=[], note_list=[]))
    d["citations"] += [
        _base("C0001", source_handle="hS0001", page="folio 1", confidence=2, date=_date(),
              attribute_list=[], media_list=[_ref("hO0001", rect=[])], note_list=["hN0002"]),
        _base("C0002", source_handle="hS0001", page="folio 2", confidence=2, date=_date(),
              attribute_list=[], media_list=[], note_list=[]),
    ]
    people = [  # gid, first name, surname, gender, birth year
        ("I0001", "Lucía", "Ortega Sanz", 0, 1900),
        ("I0002", "Mateo", "Ortega Ruiz", 1, 1870),
        ("I0003", "Clara", "Sanz Vidal", 0, 1872),
        ("I0004", "Gil", "Ortega Peña", 1, 1840),
        ("I0005", "Inés", "Ruiz Lomas", 0, 1842),
        ("I0006", "Blas", "Sanz Lago", 1, 1845),
        ("I0007", "Rosa", "Vidal Prado", 0, 1846),
        ("I0008", "Tomás", "Ortega Sanz", 1, 1903),
    ]
    for n, (gid, first, surname, gender, year) in enumerate(people, 1):
        birth = f"E{n:04d}"
        d["events"].append(_base(birth, type="Birth", date=_date(year, 3, n), place="hP0001",
                                 description="", attribute_list=[], media_list=[], note_list=[],
                                 citation_list=["hC0001"] if gid == "I0001" else []))
        refs = [_ref("h" + birth, role="Primary")]
        if gid == "I0004":
            d["events"] += [
                _base("E0101", type="Death", date=_date(1910), place="hP0001", description="",
                      attribute_list=[], media_list=[], note_list=[], citation_list=[]),
                _base("E0102", type="Residence", date=_date(1880), place="hP0001", description="",
                      attribute_list=[], media_list=[], note_list=[], citation_list=["hC0002"]),
            ]
            refs += [_ref("hE0101", role="Primary"), _ref("hE0102", role="Primary")]
        d["people"].append(_base(
            gid, gender=gender, birth_ref_index=0, death_ref_index=1 if gid == "I0004" else -1,
            primary_name={"first_name": first, "type": "Birth Name", "private": False, "citation_list": [],
                          "note_list": [], "date": _date(),
                          "surname_list": [{"surname": surname, "primary": True, "prefix": "", "connector": "",
                                            "origintype": ""}]},
            alternate_names=[], event_ref_list=refs, family_list=[], parent_family_list=[],
            citation_list=[], note_list=["hN0001"] if gid == "I0001" else [], media_list=[],
            attribute_list=[], address_list=[], lds_ord_list=[], person_ref_list=[], urls=[],
        ))
    fams = [("F0001", "I0002", "I0003", ["I0001", "I0008"]),
            ("F0002", "I0004", "I0005", ["I0002"]),
            ("F0003", "I0006", "I0007", ["I0003"])]
    for n, (gid, fa, mo, kids) in enumerate(fams, 1):
        marriage = f"E02{n:02d}"
        d["events"].append(_base(marriage, type="Marriage", date=_date(1895 - 30 * (n > 1)), place="hP0001",
                                 description="", attribute_list=[], media_list=[], note_list=[], citation_list=[]))
        d["families"].append(_base(
            gid, father_handle="h" + fa, mother_handle="h" + mo, type="Married", complete=0,
            child_ref_list=[_ref("h" + k, frel="Birth", mrel="Birth") for k in kids],
            event_ref_list=[_ref("h" + marriage, role="Family")],
            attribute_list=[], citation_list=[], lds_ord_list=[], media_list=[], note_list=[],
        ))
    P = {p["gramps_id"]: p for p in d["people"]}
    for f in d["families"]:
        P[f["father_handle"][1:]]["family_list"].append(f["handle"])
        P[f["mother_handle"][1:]]["family_list"].append(f["handle"])
        for c in f["child_ref_list"]:
            P[c["ref"][1:]]["parent_family_list"].append(f["handle"])
    for o in d["people"] + d["families"] + d["events"]:
        if o["gramps_id"] in ("I0001", "F0001", "E0001"):
            o["tag_list"] = ["htag1"]
    return d


# -- plants: each takes the tree (a dict of lists) and changes it in place -------------------------

def _get(d, t, gid):
    return next(o for o in d[t] if o["gramps_id"] == gid)


def _new_person(d, gid, first, surname, *, birth=None, parent_family=None, family=None):
    p = copy.deepcopy(_get(d, "people", "I0008"))
    p.update(handle="h" + gid, gramps_id=gid, event_ref_list=[], birth_ref_index=-1, death_ref_index=-1,
             family_list=[family] if family else [], parent_family_list=[parent_family] if parent_family else [])
    p["primary_name"]["first_name"] = first
    p["primary_name"]["surname_list"][0]["surname"] = surname
    if birth:
        ev = copy.deepcopy(_get(d, "events", "E0001"))
        ev.update(handle="hE" + gid, gramps_id="E" + gid, date=_date(*birth), citation_list=[], tag_list=[])
        d["events"].append(ev)
        p["event_ref_list"] = [_ref(ev["handle"], role="Primary")]
        p["birth_ref_index"] = 0
    d["people"].append(p)
    return p


def _new_family(d, gid, father, mother, kids=()):
    f = copy.deepcopy(_get(d, "families", "F0001"))
    f.update(handle="h" + gid, gramps_id=gid, father_handle=father, mother_handle=mother, event_ref_list=[],
             tag_list=[], child_ref_list=[_ref(k, frel="Birth", mrel="Birth") for k in kids])
    d["families"].append(f)
    return f


def p_orphan_event(d):
    p = _get(d, "people", "I0004")
    p["event_ref_list"] = [r for r in p["event_ref_list"] if r["ref"] != "hE0102"]


def p_orphan_citation(d):
    d["citations"].append(_base("C0099", source_handle="hS0001", page="", confidence=2, date=_date(),
                                attribute_list=[], media_list=[], note_list=[]))


def p_orphan_note(d):
    d["notes"].append({**copy.deepcopy(d["notes"][0]), "handle": "hN0099", "gramps_id": "N0099"})


def p_orphan_media(d):
    d["media"].append({**copy.deepcopy(d["media"][0]), "handle": "hO0099", "gramps_id": "O0099"})


def p_orphan_repository(d):
    d["repositories"].append(_base("R0099", name="Archivo Imaginario", type="Archive",
                                   address_list=[], urls=[], note_list=[]))


def p_source_no_repository(d):
    d["sources"].append(_base("S0099", title="Libro inventado sin archivo", author="", pubinfo="", abbrev="",
                              attribute_list=[], media_list=[], note_list=[], reporef_list=[]))


def p_dup_media(d):
    for n, path in ((97, "ejemplo/acta-0097.jpg"), (98, "ejemplo/acta-0098-copia.jpg")):
        d["media"].append({**copy.deepcopy(d["media"][0]), "handle": f"hO00{n}", "gramps_id": f"O00{n}",
                           "path": path, "checksum": "a" * 32})


def p_dup_media_path(d):
    # the same path under two checksums (one empty): found by path alone
    for n, cs in ((95, ""), (96, "b" * 32)):
        d["media"].append({**copy.deepcopy(d["media"][0]), "handle": f"hO00{n}", "gramps_id": f"O00{n}",
                           "path": "ejemplo/acta-0095.jpg", "checksum": cs})


def p_dup_repository(d):
    d["repositories"].append({**copy.deepcopy(d["repositories"][0]), "handle": "hR0098", "gramps_id": "R0098",
                              "name": "ARCHIVO DIOCESANO DE EJEMPLO"})
    d["sources"][0]["reporef_list"].append({**copy.deepcopy(d["sources"][0]["reporef_list"][0]), "ref": "hR0098"})


def p_dup_citation(d):
    d["citations"].append(_base("C0098", source_handle="hS0001", page="Folio  1", confidence=2, date=_date(),
                                attribute_list=[], media_list=[], note_list=[]))
    _get(d, "events", "E0002")["citation_list"].append("hC0098")


def p_dangling_child(d):
    _get(d, "families", "F0002")["child_ref_list"].append(_ref("ghost-person", frel="Birth", mrel="Birth"))


def p_dangling_subref(d):
    _get(d, "families", "F0003")["child_ref_list"][0]["note_list"].append("ghost-note")


def p_place_not_place(d):
    _get(d, "events", "E0201")["place"] = "hI0004"


def p_birth_pointer_moved(d):
    _get(d, "people", "I0004")["birth_ref_index"] = 2  # the Residence


def p_repeated_child(d):
    f = _get(d, "families", "F0001")
    f["child_ref_list"].append(copy.deepcopy(f["child_ref_list"][1]))
    _get(d, "people", "I0008")["parent_family_list"].append("hF0001")


def p_second_birth(d):
    ev = copy.deepcopy(_get(d, "events", "E0001"))
    ev.update(handle="hE0099", gramps_id="E0099", place="", citation_list=[], tag_list=[])
    d["events"].append(ev)
    _get(d, "people", "I0001")["event_ref_list"].append(_ref("hE0099", role="Primary"))


def p_private(d):
    _get(d, "people", "I0007")["primary_name"]["private"] = True


def _sensitive_media(d, gid, person_gid, birth, desc):
    m = {**copy.deepcopy(d["media"][0]), "handle": "h" + gid, "gramps_id": gid, "desc": desc}
    d["media"].append(m)
    p = _new_person(d, person_gid, "Nuria", "Prado Lago", birth=birth)
    p["media_list"] = [_ref(m["handle"], rect=[])]
    return p


def p_living_media(d):
    _sensitive_media(d, "O0090", "I0090", (2001, 5, 2), "Pasaporte de Nuria")


def p_place_no_coords(d):
    _get(d, "places", "P0001")["lat"] = ""


def p_citation_ghost_source(d):
    _get(d, "citations", "C0002")["source_handle"] = "ghost-source"


def p_surname_marker(d):
    _get(d, "people", "I0007")["primary_name"]["surname_list"][0]["surname"] = "Vidal [¿?]"


def p_dup_couple(d):
    _new_family(d, "F0099", "hI0002", "hI0003")
    for gid in ("I0002", "I0003"):
        _get(d, "people", gid)["family_list"].append("hF0099")


def p_dup_person_birth(d):
    _new_person(d, "I0099", "Tomás", "Ortega Sanz", birth=(1903, 3, 0))


def p_dup_couple_names(d):
    _new_person(d, "I0097", "Gil", "Ortega Peña", family="hF0097")
    _new_person(d, "I0098", "Inés", "Ruiz Lomas", family="hF0097")
    _new_family(d, "F0097", "hI0097", "hI0098")


def p_event_no_place(d):
    _get(d, "events", "E0203")["place"] = ""


def p_dup_person_relative(d):
    _new_person(d, "I0096", "Tomás", "Ortega Sanz", parent_family="hF0096")
    _new_family(d, "F0096", "hI0002", "", kids=["hI0096"])
    _get(d, "people", "I0002")["family_list"].append("hF0096")


def p_dup_person_child(d):
    _new_person(d, "I0094", "Mateo", "Ortega Ruiz", family="hF0094")
    _new_person(d, "I0093", "Lucía", "Ortega Sanz", parent_family="hF0094")
    _new_family(d, "F0094", "hI0094", "", kids=["hI0093"])


def p_dup_person_fuzzy_year(d):
    _new_person(d, "I0092", "Juan Tomás", "Ortega Sanz", birth=(1904, 0, 0))


def p_dup_person_fuzzy_spouse(d):
    _new_person(d, "I0091", "Gil", "Ortega", family="hF0091")
    _new_family(d, "F0091", "hI0091", "hI0005")
    _get(d, "people", "I0005")["family_list"].append("hF0091")


def _namesakes(d, n, first, surname, parents_a, parents_b):
    """Two children named alike, born a year apart, each with a father and a mother of their own."""
    for k, (fam, parents) in enumerate((("F%04d" % n, parents_a), ("F%04d" % (n + 1), parents_b))):
        fa, mo, kid = ("I%04d" % (n + 3 * k + i) for i in range(3))
        _new_person(d, fa, *parents[0], family="h" + fam)
        _new_person(d, mo, *parents[1], family="h" + fam)
        _new_person(d, kid, first[k], surname, birth=(1880 + k, 0, 0), parent_family="h" + fam)
        _new_family(d, fam, "h" + fa, "h" + mo, kids=["h" + kid])


def p_dup_person_fuzzy_named_parents(d):
    _namesakes(d, 60, ("Juan", "Juan José"), "Pérez López",
               (("Ramón", "Pérez Gil"), ("Elena", "López Mar")), (("Andrés", "Pérez Soto"), ("Marta", "López Rey")))


def p_dup_person_fuzzy_same_named_parents(d):
    parents = (("Hugo", "Moreno Gil"), ("Sara", "Díaz Mar"))
    _namesakes(d, 66, ("Pablo", "Pablo Luis"), "Moreno Díaz", parents, parents)


def p_dup_person_fuzzy_same_named_mother(d):
    mother = ("Nuria", "Vidal Ros")
    _namesakes(d, 72, ("Tomás", "Tomás Javier"), "Serra Vidal",
               (("Félix", "Serra Pons"), mother), (("Ignacio", "Serra Mas"), mother))


def p_dup_person_fuzzy_same_named_father(d):
    father = ("Álvaro", "Cano Ruiz")
    _namesakes(d, 78, ("Lucía", "Lucía Inés"), "Cano Torres",
               (father, ("Rosa", "Torres Gil")), (father, ("Clara", "Torres Vela")))


def p_dup_person_fuzzy_adoptive_parents(d):
    """Other names on both sides, but one side is the adopted child of that couple: not its birth parents."""
    _namesakes(d, 84, ("Diego", "Diego Martín"), "Ortiz Lara",
               (("Rafael", "Ortiz Bravo"), ("Inés", "Lara Pino")), (("Óscar", "Ortiz Sanz"), ("Eva", "Lara Cid")))
    _get(d, "families", "F0085")["child_ref_list"][0].update(frel="Adopted", mrel="Adopted")


def p_family_backlink(d):
    _get(d, "people", "I0008")["parent_family_list"] = []


def p_empty_family(d):
    _new_family(d, "F0095", "", "")


def p_citation_no_source(d):
    _get(d, "citations", "C0002")["source_handle"] = ""


def p_birth_without_pointer(d):
    _get(d, "people", "I0005")["birth_ref_index"] = -1


def p_second_marriage(d):
    ev = copy.deepcopy(_get(d, "events", "E0201"))
    ev.update(handle="hE0098", gramps_id="E0098")
    d["events"].append(ev)
    _get(d, "families", "F0001")["event_ref_list"].append(_ref("hE0098", role="Family"))


def p_dangling_tag(d):
    _get(d, "events", "E0003")["tag_list"] = ["ghost-tag"]


def p_object_removed(d):
    d["notes"] = [n for n in d["notes"] if n["gramps_id"] != "N0001"]
    _get(d, "people", "I0001")["note_list"] = []


def p_ahnentafel_lost(d):
    _get(d, "families", "F0003")["father_handle"] = ""
    _get(d, "people", "I0006")["family_list"] = []


def p_ahnentafel_changed(d):
    _new_person(d, "I0095", "Gil", "Ortega Peñalba", family="hF0002")
    _get(d, "families", "F0002")["father_handle"] = "hI0095"
    _get(d, "people", "I0004")["family_list"] = []


def p_ahnentafel_truncated(d):
    # every couple shares the previous couple as both pairs of parents: positions double per
    # generation until they pass MAX_POSITIONS, and the comparison must say how far it looked
    kids = ["I0004", "I0005", "I0006", "I0007"]  # the four grandparents share the first couple
    for k in range(15):
        fa, mo, fam = f"I{200 + 2 * k}", f"I{201 + 2 * k}", f"F{200 + k}"
        f = copy.deepcopy(_get(d, "families", "F0003"))
        f.update(handle="h" + fam, gramps_id=fam, father_handle="h" + fa, mother_handle="h" + mo,
                 child_ref_list=[_ref("h" + c, frel="Birth", mrel="Birth") for c in kids],
                 event_ref_list=[], tag_list=[])
        d["families"].append(f)
        for c in kids:
            _get(d, "people", c)["parent_family_list"].append("h" + fam)
        _new_person(d, fa, f"Pascual{k}", "Colapso", family="h" + fam)
        _new_person(d, mo, f"Petronila{k}", "Colapso", family="h" + fam)
        kids = [fa, mo]


SELFTEST_POSITIONS = 1_000  # MAX_POSITIONS while the self-test runs; the clean tree has 7


def p_unconnected(d):
    _new_person(d, "I9001", "Aníbal", "Apartado", family="hF9001")
    _new_person(d, "I9002", "Berta", "Apartado", family="hF9001")
    _new_family(d, "F9001", "hI9001", "hI9002")


# dated plants: new people and families in the I07xx range, so they never touch another plant

def _event(d, person, gid, typ, date, *, family=None):
    ev = copy.deepcopy(_get(d, "events", "E0001"))
    ev.update(handle="h" + gid, gramps_id=gid, type=typ, date=_date(*date), citation_list=[], tag_list=[])
    d["events"].append(ev)
    if family is not None:
        family["event_ref_list"].append(_ref(ev["handle"], role="Family"))
    else:
        person["event_ref_list"].append(_ref(ev["handle"], role="Primary"))
        if typ == "Death":
            person["death_ref_index"] = len(person["event_ref_list"]) - 1


def _parent_child(d, fam, parent, child, *, mother=False, parent_birth, child_birth, gender=None):
    """A one-parent family `fam` with `parent` and one child, both new, born on the dates given."""
    par = _new_person(d, parent, "Plantado", f"Padre {parent}", birth=parent_birth, family="h" + fam)
    if mother or gender == 0:
        par["gender"] = 0
    kid = _new_person(d, child, "Plantado", f"Hijo {child}", birth=child_birth, parent_family="h" + fam)
    f = _new_family(d, fam, "" if mother else par["handle"], par["handle"] if mother else "", [kid["handle"]])
    return par, kid, f


def p_death_before_birth(d):
    p = _new_person(d, "I0700", "Plantado", "Orden", birth=(1850, 5, 1))
    _event(d, p, "E0701", "Death", (1849,))


def p_child_before_parent(d):
    _parent_child(d, "F0702", "I0702", "I0703", parent_birth=(1880,), child_birth=(1879, 4, 2))


def p_child_after_mother_death(d):
    par, _, _ = _parent_child(d, "F0704", "I0704", "I0705", mother=True, parent_birth=(1850,),
                              child_birth=(1881, 6, 1))
    _event(d, par, "E0706", "Death", (1880, 1, 1))


def p_marriage_before_birth(d):
    par, _, f = _parent_child(d, "F0707", "I0707", "I0708", parent_birth=(1900,), child_birth=(1930,))
    _event(d, par, "E0709", "Marriage", (1890,), family=f)


def p_marriage_after_death(d):
    par, _, f = _parent_child(d, "F0710", "I0710", "I0711", parent_birth=(1850,), child_birth=(1876,))
    _event(d, par, "E0712", "Death", (1880,))
    _event(d, par, "E0713", "Marriage", (1885,), family=f)


def p_own_ancestor(d):
    _, kid, f = _parent_child(d, "F0714", "I0714", "I0715", parent_birth=(1850,), child_birth=(1880,))
    _new_family(d, "F0716", kid["handle"], "", ["hI0714"])
    kid["family_list"].append("hF0716")
    _get(d, "people", "I0714")["parent_family_list"].append("hF0716")


def p_parent_age(d):
    _parent_child(d, "F0717", "I0717", "I0718", mother=True, parent_birth=(1880, 1, 1), child_birth=(1890, 6, 1))


def p_marriage_age(d):
    par, _, f = _parent_child(d, "F0719", "I0719", "I0720", mother=True, parent_birth=(1880, 1, 1),
                              child_birth=(1900,))
    _event(d, par, "E0721", "Marriage", (1891, 6, 1), family=f)


def p_sibling_spacing(d):
    par, _, f = _parent_child(d, "F0722", "I0722", "I0723", mother=True, parent_birth=(1875,),
                              child_birth=(1900, 1, 1))
    kid = _new_person(d, "I0724", "Plantada", "Hija I0724", birth=(1900, 6, 1), parent_family="hF0722")
    f["child_ref_list"].append(_ref(kid["handle"], frel="Birth", mrel="Birth"))


def p_lifespan(d):
    p = _new_person(d, "I0725", "Plantado", "Longevo", birth=(1700,))
    _event(d, p, "E0726", "Death", (1820,))


# (plant, check that must fire, key it must carry, needs the clean tree as --since and I0001 as root)
PLANTS = [
    (p_unconnected, "unconnected", "I9001", False),
    (p_orphan_event, "orphan-event", "E0102", False),
    (p_orphan_citation, "orphan-citation", "C0099", False),
    (p_orphan_note, "orphan-note", "N0099", False),
    (p_orphan_media, "orphan-media", "O0099", False),
    (p_orphan_repository, "orphan-repository", "R0099", False),
    (p_source_no_repository, "info:sources without repository", "S0099", False),
    (p_dup_repository, "dup-repository", "R0001,R0098", False),
    (p_dup_media, "dup-media", "O0097,O0098", False),
    (p_dup_media_path, "dup-media", "O0095,O0096", False),
    (p_dup_citation, "dup-citation", "C0001,C0098", False),
    (p_dangling_child, "dangling-ref", "F0002 child_ref_list -> people ghost-person", False),
    (p_dangling_subref, "dangling-ref", "F0003 child_ref_list[I0003].note_list -> notes ghost-note", False),
    (p_place_not_place, "place-not-place", "E0201 place", False),
    (p_birth_pointer_moved, "birth-death-pointer", "I0004 birth_ref_index", False),
    (p_repeated_child, "repeated-ref", "F0001 child_ref_list I0008", False),
    (p_repeated_child, "repeated-ref", "I0008 parent_family_list F0001", False),
    (p_second_birth, "dup-vital-event", "I0001 Birth E0001,E0099", False),
    (p_private, "private", "I0007 primary_name", False),
    (p_living_media, "living-media-sensitive", "I0090 O0090", False),
    (p_place_no_coords, "place-no-coords", "P0001", False),
    (p_citation_ghost_source, "dangling-ref", "C0002 source_handle -> sources ghost-source", False),
    (p_surname_marker, "surname-marker", "I0007 Vidal [¿?]", False),
    (p_dup_couple, "dup-couple", "F0001,F0099", False),
    (p_dup_person_birth, "dup-person-birth", "I0008,I0099", False),
    (p_dup_couple_names, "dup-couple-names", "F0002,F0097", False),
    (p_event_no_place, "info:events without place", "E0203", False),
    (p_dup_person_relative, "dup-person-relative", "I0008,I0096", False),
    (p_dup_person_child, "dup-person-relative", "I0002,I0094", False),
    (p_dup_person_fuzzy_year, "dup-person-fuzzy", "I0008,I0092", False),
    (p_dup_person_fuzzy_spouse, "dup-person-fuzzy", "I0004,I0091", False),
    (p_dup_person_fuzzy_named_parents, "info:dup-person-fuzzy skipped, parents differ by name", "I0062,I0065", False),
    (p_dup_person_fuzzy_same_named_parents, "dup-person-fuzzy", "I0068,I0071", False),
    (p_dup_person_fuzzy_same_named_mother, "dup-person-fuzzy", "I0074,I0077", False),
    (p_dup_person_fuzzy_same_named_father, "dup-person-fuzzy", "I0080,I0083", False),
    (p_dup_person_fuzzy_adoptive_parents, "dup-person-fuzzy", "I0086,I0089", False),
    (p_family_backlink, "family-backlink", "F0001 child I0008", False),
    (p_empty_family, "empty-family", "F0095", False),
    (p_citation_no_source, "citation-no-source", "C0002", False),
    (p_birth_without_pointer, "birth-death-pointer", "I0005 birth_ref_index", False),
    (p_second_marriage, "dup-vital-event", "F0001 Marriage E0098,E0201", False),
    (p_dangling_tag, "dangling-ref", "E0003 tag_list -> tags ghost-tag", False),
    (p_object_removed, "missing-object", "notes N0001", True),
    (p_ahnentafel_lost, "ahnentafel-lost", "6 I0006", True),
    (p_ahnentafel_changed, "ahnentafel-changed", "4 I0004", True),
    (p_ahnentafel_truncated, "ahnentafel-truncated", "root I0001", True),
    (p_death_before_birth, "event-order", "I0700 EI0700 E0701", False),
    (p_child_before_parent, "child-before-parent", "F0702 I0702 I0703", False),
    (p_child_after_mother_death, "child-after-parent-death", "F0704 I0704 I0705", False),
    (p_marriage_before_birth, "marriage-before-birth", "F0707 E0709 I0707", False),
    (p_marriage_after_death, "marriage-after-death", "F0710 E0713 I0710", False),
    (p_own_ancestor, "own-ancestor", "I0714,I0715", False),
    (p_parent_age, "parent-age", "F0717 I0717 I0718", False),
    (p_marriage_age, "marriage-age", "F0719 E0721 I0719", False),
    (p_sibling_spacing, "sibling-spacing", "I0722 I0723,I0724", False),
    (p_lifespan, "lifespan", "I0725", False),
]


SKIPPED = "dup-person-fuzzy skipped, parents differ by name"

# left out of the all-in-one tree because they undo another plant there: the lost backlink hides the
# repeated child, the empty source hides the ghost source, the new grandfather breaks the repeated couple
TOGETHER_SKIP = (p_family_backlink, p_citation_no_source, p_ahnentafel_changed)


def _run(data: dict, clean: dict):
    from .cli import check  # late import: cli imports this module for the selftest command

    base = Tree(copy.deepcopy(clean), source="clean synthetic tree")
    report = check(Tree(data, source="planted synthetic tree"), since=base, root="I0001")
    got = {(f["check"], f["key"]) for f in report["findings"]}
    no_place = report["info"].get("events without place, in scope", [])
    # events without place is a count, not a finding: compare it with the clean tree's
    got |= {("info:events without place", e) for e in no_place}
    got |= {("info:events without place", e["gramps_id"]) for e in data["events"] if not e.get("place")}
    if report["info"].get("sources without repository"):  # a count too, so the ids come from the tree
        got |= {("info:sources without repository", s["gramps_id"]) for s in data["sources"] if not s.get("reporef_list")}
    # the pairs dup-person-fuzzy left out are listed in info, so a plant can look for them
    got |= {("info:" + SKIPPED, k) for k in report["info"].get(SKIPPED, [])}
    return got, report


def selftest(verbose: bool = False) -> int:
    # the collapse plant doubles positions per generation up to the cap; a small cap trips the same
    # truncation in a fraction of the time, and no other plant comes near it
    cap, compare.MAX_POSITIONS = compare.MAX_POSITIONS, min(compare.MAX_POSITIONS, SELFTEST_POSITIONS)
    try:
        return _selftest(verbose)
    finally:
        compare.MAX_POSITIONS = cap


def _selftest(verbose: bool) -> int:
    clean = clean_tree()
    say = print if verbose else (lambda *a, **k: None)
    failed = 0

    got, report = _run(copy.deepcopy(clean), clean)
    if got:
        failed += 1
        say(f"FAIL positive control: the clean tree gave {sorted(got)}")
    else:
        say("ok   positive control: the clean tree gives no finding")

    together = copy.deepcopy(clean)
    done = set()
    for plant, check_name, key, _ in PLANTS:
        data = copy.deepcopy(clean)
        plant(data)
        got, _ = _run(data, clean)
        hit = (check_name, key) in got
        failed += not hit
        say(f"{'ok  ' if hit else 'FAIL'} {plant.__name__[2:]}: {check_name} {key}"
            + ("" if hit else f"  (got {sorted(got)})"))
        if plant not in done and plant not in TOGETHER_SKIP:
            plant(together)
            done.add(plant)

    got, _ = _run(together, clean)
    expected = {(c, k) for p, c, k, _ in PLANTS if p in done}
    missed = expected - got
    failed += bool(missed)
    say(f"{'ok  ' if not missed else 'FAIL'} all {len(done)} plants in one tree: {len(expected - missed)} of "
        f"{len(expected)} found" + (f", missed {sorted(missed)}" if missed else ""))
    say(f"\n{'FAIL' if failed else 'OK'}: {len(PLANTS)} planted errors, {failed} problem(s)")
    return 1 if failed else 0
