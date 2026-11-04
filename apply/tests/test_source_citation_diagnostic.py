"""Sources are evidence containers, not direct citation carriers in Gramps."""
from gwt_apply.ops import validate


def citation(attach):
    return {'op':'add_citation','key':'invented-citation','ruling':'invented-review',
            'source':'S0001','page':'book 1, folio 7','attach':attach}


def test_source_citation_rejection_explains_supported_alternatives():
    errors=validate([citation([{'source':'S0001'}])])
    message=' '.join(errors)
    assert 'Source' in message
    assert 'cannot carry a citation' in message
    assert 'Gramps' in message
    assert 'add_note' in message
    assert 'person' in message and 'event' in message
    assert len(errors)==1


def test_source_is_not_a_citation_target_even_in_mixed_targets():
    assert validate([citation([{'person':'I0001'},{'source':'S0001'}])])


def test_orphan_citation_guard_stays_closed():
    assert validate([citation([])])


def test_document_source_without_a_person_using_a_note():
    op={'op':'add_note','key':'invented-source-reading','ruling':'invented-review',
        'text':'Unresolved reading: preserve alternatives without assigning identity.',
        'attach':[{'source':'S0001'}]}
    assert validate([op])==[]


def test_existing_supported_citation_carriers_still_pass():
    for target in ({'person':'I0001'},{'family':'F0001'},{'event':'E0001'},{'media':'O0001'},{'place':'P0001'}):
        assert validate([citation([target])])==[]


def test_detaching_a_citation_from_a_source_explains_the_alternatives():
    op={'op':'detach','key':'invented-detach','ruling':'invented-review',
        'target':{'source':'S0001'},'object':{'citation':'C0001'}}
    errors=validate([op])
    message=' '.join(errors)
    assert 'Source' in message
    assert 'cannot carry a citation' in message
    assert 'add_note' in message
    assert len(errors)==1
