import copy
import json

import pytest

from gwt_check.cli import main
from gwt_check.component_evidence import component_evidence
from gwt_check.tree import TYPES, BadInput, Tree


def fixture():
    """Main group F1: I1 with children I2 and I6. Batch F2: I3 with child I4. I5 is in no family."""
    d = {t: [] for t in TYPES}
    for h, gid in (('a', 'I1'), ('b', 'I2'), ('c', 'I3'), ('d', 'I4'), ('e', 'I5'), ('g', 'I6')):
        d['people'].append(dict(handle=h, gramps_id=gid, family_list=[], parent_family_list=[]))
    d['families'] = [dict(handle='f1', gramps_id='F1', father_handle='a',
                          child_ref_list=[dict(ref='b'), dict(ref='g')]),
                     dict(handle='f2', gramps_id='F2', father_handle='c', child_ref_list=[dict(ref='d')])]
    for typ, h in [('citations', 'c1'), ('citations', 'c2'), ('notes', 'n1'), ('media', 'm1')]:
        d[typ].append(dict(handle=h, gramps_id=h.upper()))
    d['events'] = [dict(handle='e1', gramps_id='E1')]
    return d


def person(d, gid):
    return next(p for p in d['people'] if p['gramps_id'] == gid)


def put(obj, kind, ref):
    obj.setdefault(kind, []).append(dict(ref=ref) if kind == 'media_list' else ref)


def bridges(d, root=None):
    return [([g['key'] for g in r['groups']], r['citations'] + r['notes'] + r['media'])
            for r in component_evidence(Tree(d), root)['rows']]


@pytest.mark.parametrize('kind,ref', [('citation_list', 'c1'), ('note_list', 'n1'), ('media_list', 'm1')])
@pytest.mark.parametrize('via_event', [False, True])
def test_family_and_family_event_evidence_is_shared(kind, ref, via_event):
    d = fixture()
    put(d['events'][0] if via_event else d['families'][0], kind, ref)
    if via_event:
        d['families'][0]['event_ref_list'] = [dict(ref='e1', role='Family')]
    put(person(d, 'I4'), kind, ref)
    out = component_evidence(Tree(d))
    assert [[g['key'] for g in r['groups']] for r in out['rows']] == [['I1', 'I3']]
    assert out['components'] == 2  # shared evidence joins no one


def test_only_primary_and_family_roles_hold_an_events_evidence():
    d = fixture()
    put(person(d, 'I2'), 'citation_list', 'c1')
    put(d['events'][0], 'citation_list', 'c1')
    person(d, 'I4')['event_ref_list'] = [dict(ref='e1', role='Godfather')]
    assert bridges(d) == []  # a godparent holds nothing of the act
    person(d, 'I4')['event_ref_list'] = [dict(ref='e1', role={'string': 'Primary'})]
    assert bridges(d) == [(['I1', 'I3'], ['C1'])]
    del person(d, 'I4')['event_ref_list']
    d['families'][1]['event_ref_list'] = [dict(ref='e1', role='Primary')]
    assert bridges(d) == []
    d['families'][1]['event_ref_list'] = [dict(ref='e1', role='Family')]
    assert bridges(d) == [(['I1', 'I3'], ['C1'])]


def test_people_in_no_family_are_left_out():
    d = fixture()
    put(person(d, 'I1'), 'citation_list', 'c1')
    put(person(d, 'I5'), 'citation_list', 'c1')
    out = component_evidence(Tree(d))
    assert out['rows'] == [] and out['components'] == 2


def test_a_one_sided_family_pointer_joins_no_one():
    d = fixture()
    put(d['families'][0], 'note_list', 'n1')
    put(person(d, 'I4'), 'note_list', 'n1')
    person(d, 'I3')['family_list'] = ['f1']  # F1 does not list I3 back
    assert bridges(d) == [(['I1', 'I3'], ['N1'])]


def test_notes_and_media_of_a_citation_count():
    d = fixture()
    d['citations'][0].update(note_list=['n1'], media_list=[dict(ref='m1')])
    d['citations'][1].update(note_list=['n1'], media_list=[dict(ref='m1')])
    put(person(d, 'I1'), 'citation_list', 'c1')
    put(person(d, 'I3'), 'citation_list', 'c2')
    assert bridges(d) == [(['I1', 'I3'], ['N1', 'M1'])]


def test_one_row_per_set_of_groups_in_id_order():
    d = fixture()
    person(d, 'I3')['gramps_id'] = 'I10000'
    person(d, 'I4')['gramps_id'] = 'I9001'
    d['people'] += [dict(handle='x', gramps_id='I7'), dict(handle='y', gramps_id='I8')]
    d['families'].append(dict(handle='f3', gramps_id='F3', father_handle='x', mother_handle='y'))
    for h in ('a', 'c', 'x'):
        put(next(p for p in d['people'] if p['handle'] == h), 'citation_list', 'c2')
    for h in ('c', 'x'):
        put(next(p for p in d['people'] if p['handle'] == h), 'note_list', 'n1')
    put(person(d, 'I9001'), 'citation_list', 'c1')
    put(person(d, 'I8'), 'citation_list', 'c1')
    out = component_evidence(Tree(d))
    assert out['main'] == {'key': 'I1', 'people': 3, 'main': True}
    assert [([g['key'] for g in r['groups']], r['citations'], r['notes']) for r in out['rows']] == [
        (['I1', 'I7', 'I9001'], ['C2'], []),
        (['I7', 'I9001'], ['C1'], ['N1'])]


def test_rows_touching_the_main_group_come_first_and_evidence_is_in_id_order():
    d = fixture()
    d['people'] += [dict(handle='x', gramps_id='I7'), dict(handle='y', gramps_id='I8')]
    d['families'].append(dict(handle='f3', gramps_id='F3', father_handle='x', mother_handle='y'))
    d['citations'] += [dict(handle='c10', gramps_id='C10'), dict(handle='c9', gramps_id='C9')]
    for h in ('a', 'x'):  # I1 + I7: a row that does not touch the main group
        put(next(p for p in d['people'] if p['handle'] == h), 'citation_list', 'c1')
    for h in ('c', 'x'):  # I3 + I7: the root's group is the main one
        for c in ('c10', 'c9'):
            put(next(p for p in d['people'] if p['handle'] == h), 'citation_list', c)
    assert bridges(d, 'I4') == [(['I3', 'I7'], ['C9', 'C10']), (['I1', 'I7'], ['C1'])]


def test_root_picks_the_main_group_and_a_root_in_no_family_falls_back():
    d = fixture()
    put(person(d, 'I2'), 'citation_list', 'c1')
    put(person(d, 'I3'), 'citation_list', 'c1')
    out = component_evidence(Tree(d), 'I4')
    assert out['main'] == {'key': 'I3', 'people': 2, 'main': True}
    assert [g['key'] for g in out['rows'][0]['groups']] == ['I3', 'I1']
    assert component_evidence(Tree(d), 'I5')['main']['key'] == 'I1'
    with pytest.raises(BadInput, match='I99'):
        component_evidence(Tree(d), 'I99')


def test_sources_and_dangling_references_do_not_bridge():
    d = fixture()
    d['sources'] = [dict(handle='s', gramps_id='S1')]
    d['citations'][0]['source_handle'] = d['citations'][1]['source_handle'] = 's'
    person(d, 'I1').update(citation_list=['c1', 'missing'], family_list=['missing'])
    person(d, 'I3').update(citation_list=['c2', 'missing'])
    assert bridges(d) == []
    for gid in ('I1', 'I3'):  # a dangling or malformed note, media or event ref is skipped, not a crash
        person(d, gid).update(note_list=['missing'], media_list=[dict(ref='missing'), 'm1'],
                              event_ref_list=[dict(ref='missing', role='Primary'), 'e1'])
    assert bridges(d) == []


def test_input_unchanged_partial_refused_and_null_lists_read():
    d = fixture()
    before = copy.deepcopy(d)
    component_evidence(Tree(d))
    assert d == before
    with pytest.raises(BadInput):
        component_evidence(Tree(d, partial=True))
    d['families'][1]['child_ref_list'] = None
    d['families'].append(dict(handle='f9', gramps_id='F9', child_ref_list=['b']))
    person(d, 'I1').update(citation_list=None, note_list=None, media_list=None, event_ref_list=None)
    assert component_evidence(Tree(d))['rows'] == []


def test_cli_text_json_limit_and_exit_codes(tmp_path, capsys):
    d = fixture()
    put(person(d, 'I2'), 'citation_list', 'c1')
    put(person(d, 'I4'), 'citation_list', 'c1')
    put(person(d, 'I2'), 'note_list', 'n1')
    put(person(d, 'I4'), 'note_list', 'n1')
    p = tmp_path / 'tree.json'
    p.write_text(json.dumps({'objects': d}))
    assert main(['component-evidence', str(p)]) == 0
    out = capsys.readouterr().out
    assert '  I1 (3, main) + I3 (2): C1, N1\n' in out
    assert main(['component-evidence', str(p), '--limit', '0']) == 0
    out = capsys.readouterr().out
    assert '... 1 more' in out and 'C1' not in out
    assert main(['component-evidence', str(p), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['rows'][0]['notes'] == ['N1']
    assert main(['component-evidence', str(p), '--root', 'I99']) == 2
    assert 'I99' in capsys.readouterr().err
