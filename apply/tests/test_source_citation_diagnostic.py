"""A refused carrier names the kinds that can carry the object, and an attached citation points at add_note."""
from gwt_apply.ops import validate

CITATION_CARRIERS = "association, child_ref, event, family, media, person, place"


def citation(target):
    return {"op": "add_citation", "key": "c", "ruling": "invented-review", "source": "S0001", "page": "folio 7",
            "attach": [target]}


def test_a_citation_on_a_non_carrier_names_the_carriers_and_the_note_route():
    for kind, cls in (("source", "Source"), ("repository", "Repository"), ("citation", "Citation")):
        errors = validate([citation({kind: "X0001"})])
        assert errors == [f"op 1 (add_citation 'c'): a {cls} cannot carry a citation; a citation goes on one of: "
                          f"{CITATION_CARRIERS}; a reading you cannot tie to one of these yet goes in add_note on the {kind}"]


def test_detach_names_the_carriers_without_the_note_route():
    op = {"op": "detach", "key": "d", "ruling": "invented-review", "target": {"source": "S0001"},
          "object": {"citation": "C0001"}}
    assert validate([op]) == ["op 1 (detach 'd'): a Source cannot carry a citation; a citation goes on one of: "
                              f"{CITATION_CARRIERS}"]


def test_a_media_on_a_non_carrier_names_the_media_carriers():
    op = {"op": "attach_existing", "key": "m", "ruling": "invented-review", "object": {"media": "O0001"},
          "attach": [{"repository": "R0001"}]}
    assert validate([op]) == ["op 1 (attach_existing 'm'): a Repository cannot carry a media; a media goes on one of: "
                              "citation, event, family, person, place, source"]
