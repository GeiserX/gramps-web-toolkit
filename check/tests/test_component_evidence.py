import copy
import json

import pytest

from gwt_check.component_evidence import component_evidence
from gwt_check.tree import BadInput, Tree, TYPES
from gwt_check.cli import main


def fixture():
    d = {t: [] for t in TYPES}
    for h in ('a', 'b', 'c', 'd'):
        d['people'].append(dict(handle=h, gramps_id='I'+h, family_list=[], parent_family_list=[]))
    d['families'] = [dict(handle='f', gramps_id='F1', father_handle='a',
                          child_ref_list=[dict(ref='b')])]
    for typ, h in [('citations', 'c1'), ('notes', 'n1'), ('media', 'm1')]:
        d[typ].append(dict(handle=h, gramps_id=h.upper()))
    d['events'] = [dict(handle='e', gramps_id='E1', citation_list=['c1'],
                        note_list=['n1'], media_list=[dict(ref='m1')])]
    return d


@pytest.mark.parametrize('kind,ref', [('citation_list','c1'), ('note_list','n1'), ('media_list','m1')])
@pytest.mark.parametrize('via_event', [False, True])
def test_family_and_family_event_evidence_is_included(kind, ref, via_event):
    d = fixture()
    target = 'events' if via_event else 'families'
    d[target][0][kind] = [dict(ref=ref)] if kind == 'media_list' else [ref]
    if via_event:
        d['families'][0]['event_ref_list'] = [dict(ref='e')]
    d['people'][2][kind] = [dict(ref=ref)] if kind == 'media_list' else [ref]
    out = component_evidence(Tree(d), 'Ia')
    row = next(r for r in out['shared_evidence'] if r['id'] == ref.upper())
    assert row['people'] == ['Ia', 'Ib', 'Ic']
    assert len(out['components']) == 3  # shared evidence did not join components


def test_person_only_family_pointer_and_nonrecursive_attribution():
    d = fixture()
    d['families'][0]['note_list'] = ['n1']
    d['families'][0].pop('father_handle')
    d['people'][0]['family_list'] = ['f']
    d['people'][2]['note_list'] = ['n1']
    # b's other family must not give its evidence to a through b.
    d['families'].append(dict(handle='f2', gramps_id='F2', father_handle='b', note_list=['private']))
    d['notes'].append(dict(handle='private', gramps_id='N2'))
    d['people'][3]['note_list'] = ['private']
    out = component_evidence(Tree(d), 'Ia')
    assert next(r for r in out['shared_evidence'] if r['id'] == 'N1')['people'] == ['Ia','Ib','Ic']
    assert next(r for r in out['shared_evidence'] if r['id'] == 'N2')['people'] == ['Ib','Id']


def test_source_only_and_dangling_references_do_not_join_evidence():
    d = fixture()
    d['sources'] = [dict(handle='s', gramps_id='S1')]
    d['citations'][0]['source_handle'] = 's'
    d['citations'].append(dict(handle='c2', gramps_id='C2', source_handle='s'))
    d['people'][0].update(citation_list=['c1'], family_list=['missing'])
    d['people'][2]['citation_list'] = ['c2']
    assert component_evidence(Tree(d), 'Ia')['shared_evidence'] == []


def test_input_unchanged_and_bad_root_partial_refused():
    d = fixture(); before = copy.deepcopy(d)
    component_evidence(Tree(d), 'Ia')
    assert d == before
    with pytest.raises(BadInput):
        component_evidence(Tree(d), 'missing')
    with pytest.raises(BadInput):
        component_evidence(Tree(d, partial=True), 'Ia')


def test_cli_advisory_exit_and_invalid_root(tmp_path, capsys):
    p = tmp_path / 'tree.json'; p.write_text(json.dumps({'objects':fixture()}))
    assert main(['component-evidence', str(p), '--root', 'Ia']) == 0
    assert 'shared_evidence' in json.loads(capsys.readouterr().out)
    assert main(['component-evidence', str(p), '--root', 'missing']) == 2
