"""Synthetic repeated-POST shapes, not real family records or identifiers."""
import copy
import json
import pytest
from gwt_check import checks, selftest
from gwt_check.tree import BadInput, Tree
from gwt_check.act_duplicates import same_act_report
from gwt_check.cli import main


def fixture(count=2, kind='Baptism'):
    data = selftest.clean_tree()
    for i in range(count):
        person = selftest._new_person(data, f'I{910+i}', 'Invented', 'Alphard')
        selftest._event(data, person, f'E{910+i}', kind, (1800, 1, 1))
        event = data['events'][-1]
        event['citation_list'] = ['hC0001']
    return data


def hits(data):
    return same_act_report(Tree(data))['rows']


def test_distinct_post_objects_same_act_and_spelling_variant():
    d = fixture()
    d['people'][-1]['primary_name']['surname_list'][0]['surname'] = 'Alphars'
    before = copy.deepcopy(d)
    [r] = hits(d)
    assert r['key'] == 'I910,I911'
    assert 'backup and checkpoint required' in r['detail']
    assert d == before
    d['people'].reverse(); d['events'].reverse()
    assert hits(d) == [r]


@pytest.mark.parametrize('kind', ['Birth', 'Baptism', 'Death', 'Burial'])
def test_each_individual_act_can_support_review(kind):
    assert len(hits(fixture(kind=kind))) == 1


def test_triple_post_reports_three_pairs_not_an_automatic_survivor():
    rows = hits(fixture(3))
    assert {r['key'] for r in rows} == {'I910,I911', 'I910,I912', 'I911,I912'}
    assert all('no survivor selected' in r['detail'] for r in rows)


@pytest.mark.parametrize('field,value', [('place', 'hP0002'), ('type', 'Death')])
def test_distinct_event_is_not_same_act(field, value):
    d = fixture(); d['events'][-1][field] = value
    assert hits(d) == []


@pytest.mark.parametrize('field,value', [('modifier', 1), ('quality', 1), ('calendar', 1),
    ('newyear', 1), ('dateval', [0, 1, 1800, False]), ('dateval', [1, 1, 1800, True]),
    ('dateval', [2, 1, 1800, False])])
def test_different_or_uncertain_dates_are_not_same_act(field, value):
    d = fixture(); d['events'][-1]['date'][field] = value
    assert hits(d) == []


@pytest.mark.parametrize('page', ['folio 2', 'folio 1v', 'Folio 1', ''])
def test_same_name_different_literal_page_is_not_same_record(page):
    d = fixture()
    cite = copy.deepcopy(d['citations'][0]); cite.update(handle='hC900', gramps_id='C900', page=page)
    d['citations'].append(cite); d['events'][-1]['citation_list'] = ['hC900']
    assert hits(d) == []


def test_different_source_unknown_place_or_missing_citation_stays_quiet():
    for change in ('source', 'place', 'cite'):
        d = fixture()
        if change == 'source':
            cite = copy.deepcopy(d['citations'][0]); cite.update(handle='hC900', source_handle='missing')
            d['citations'].append(cite); d['events'][-1]['citation_list'] = ['hC900']
        elif change == 'place':
            d['events'][-1]['place'] = 'missing'
        else:
            d['events'][-1]['citation_list'] = []
        assert hits(d) == []


def test_shared_marriage_parent_couple_and_witnesses_are_not_duplicate_people():
    assert hits(fixture(kind='Marriage')) == []
    d = fixture(); a, b = d['people'][-2:]
    d['families'].append(selftest._base('F900', father_handle=a['handle'], mother_handle=b['handle'], child_ref_list=[]))
    assert hits(d) == []
    d['families'].pop()
    b['event_ref_list'][0]['role'] = 'Witness'
    assert hits(d) == []


def test_parent_child_pair_is_not_a_same_act_identity_hint():
    d = fixture(); a, b = d['people'][-2:]
    d['families'].append(selftest._base('F900', father_handle=a['handle'], mother_handle='',
                                     child_ref_list=[selftest._ref(b['handle'])]))
    assert hits(d) == []


@pytest.mark.parametrize('field,value', [('first_name', 'Different'), ('surname', 'Othername')])
def test_different_primary_name_is_not_identity(field, value):
    d = fixture(); name = d['people'][-1]['primary_name']
    if field == 'surname': name['surname_list'][0]['surname'] = value
    else: name[field] = value
    assert hits(d) == []


def test_known_gender_conflict_is_not_identity():
    d = fixture(); d['people'][-1]['gender'] = 0; d['people'][-2]['gender'] = 1
    assert hits(d) == []


def test_report_is_separate_from_normal_checks_and_selftest(monkeypatch):
    d = fixture()
    assert hits(d)
    assert 'same-act-person' not in checks.CHECKS
    assert 'same-act-person' not in checks.INFO_CHECKS
    import gwt_check.act_duplicates as detector
    def forbidden(*args, **kwargs):
        pytest.fail('same-act must remain outside normal checks and selftest')
    monkeypatch.setattr(detector, 'same_act_report', forbidden)
    assert all(r['check'] != 'same-act-person' for r in checks.run_checks(Tree(d))[0])
    from gwt_check.cli import check
    assert all(r['check'] != 'same-act-person' for r in check(Tree(d))['findings'])
    assert selftest.selftest() == 0


def test_repeated_burial_on_one_person_is_not_a_person_pair():
    d = fixture(1, kind='Burial'); p = d['people'][-1]
    e = copy.deepcopy(d['events'][-1]); e.update(handle='hE999', gramps_id='E999')
    d['events'].append(e); p['event_ref_list'].append(selftest._ref('hE999', role='Primary'))
    assert hits(d) == []


def test_invalid_calendar_day_is_not_exact_evidence():
    d = fixture()
    for e in d['events'][-2:]: e['date']['dateval'] = [31, 2, 1800, False]
    assert hits(d) == []


def test_duplicate_citation_objects_same_literal_reference_still_match():
    d = fixture()
    c = copy.deepcopy(d['citations'][0]); c.update(handle='hC901', gramps_id='C901')
    d['citations'].append(c); d['events'][-1]['citation_list'] = ['hC901']
    assert len(hits(d)) == 1


def test_same_citation_only_on_person_is_not_an_event_claim():
    d = fixture()
    for p, e in zip(d['people'][-2:], d['events'][-2:]):
        e['citation_list'] = []; p['citation_list'] = ['hC0001']
    assert hits(d) == []


def test_different_existing_sources_do_not_match():
    d = fixture()
    source = copy.deepcopy(d['sources'][0]); source.update(handle='hS900', gramps_id='S900')
    d['sources'].append(source)
    cite = copy.deepcopy(d['citations'][0]); cite.update(handle='hC900', gramps_id='C900', source_handle='hS900')
    d['citations'].append(cite); d['events'][-1]['citation_list'] = ['hC900']
    assert hits(d) == []


@pytest.mark.parametrize('json_output', [False, True])
def test_cli_dump_findings_never_fail(tmp_path, capsys, monkeypatch, json_output):
    import gwt_check.cli as cli
    data = fixture(3)
    path = tmp_path / 'invented.json'
    path.write_text(json.dumps({'objects': data}))
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('a dump report must not fetch or run the normal gate')
    monkeypatch.setattr(cli, 'live_dump', forbidden)
    monkeypatch.setattr(cli, 'check', forbidden)
    monkeypatch.setattr(cli, 'run_checks', forbidden)
    assert main(['same-act', str(path)] + (['--json'] if json_output else [])) == 0
    output = capsys.readouterr().out
    if json_output:
        assert json.loads(output) == same_act_report(Tree(data))
    else:
        assert '3 same-act candidate pairs' in output
        assert 'I910,I911' in output and 'candidate only' in output
    assert path.read_bytes() == before


def test_cli_live_uses_existing_reader_only(monkeypatch, capsys):
    import gwt_check.cli as cli
    calls = []
    def fake_live(args):
        calls.append(vars(args))
        return {'objects': fixture()}
    monkeypatch.setattr(cli, 'live_dump', fake_live)
    assert main(['same-act', '--json', '--live', '--pagesize', '17', '--workers', '2',
                 '--config', 'invented.conf']) == 0
    assert len(json.loads(capsys.readouterr().out)['rows']) == 1
    assert len(calls) == 1
    assert calls[0]['live'] and calls[0]['pagesize'] == 17 and calls[0]['workers'] == 2
    assert calls[0]['config'] == 'invented.conf'


def test_cli_limit_and_empty_report(tmp_path, capsys):
    path = tmp_path / 'invented.json'
    path.write_text(json.dumps(fixture(3)))
    assert main(['same-act', str(path), '--limit', '1']) == 0
    output = capsys.readouterr().out
    assert '3 same-act candidate pairs' in output
    assert 'I910,I911' in output and 'I910,I912' not in output and 'I911,I912' not in output
    path.write_text(json.dumps(selftest.clean_tree()))
    assert main(['same-act', str(path)]) == 0
    assert '0 same-act candidate pairs' in capsys.readouterr().out


def test_partial_tree_and_bad_cli_input_are_errors(tmp_path, capsys):
    with pytest.raises(BadInput, match='no same-act evidence'):
        same_act_report(Tree(fixture(), partial=True, source='invented.gramps'))
    path = tmp_path / 'invented.gramps'
    path.write_text('<database><people/></database>')
    assert main(['same-act', str(path)]) == 2
    assert 'no same-act evidence' in capsys.readouterr().err
    assert main(['same-act', str(tmp_path / 'missing.json')]) == 2
    assert 'gwt-check:' in capsys.readouterr().err


@pytest.mark.parametrize('surname,expected', [
    ('Alphard', True), ('Alphars', True), ('Alphards', True), ('Alphar', True),
    ('Alphxrd', True), ('Alpxard', True), ('Alpxrd', False), ('Alphardxyz', False),
    ('Othername', False), ('', False),
])
def test_surname_edit_bound(surname, expected):
    data = fixture()
    data['people'][-1]['primary_name']['surname_list'][0]['surname'] = surname
    assert bool(hits(data)) == expected


@pytest.mark.parametrize('first,second,expected', [('Abcd', 'Abce', False), ('Abcd', 'Abcd', True),
    ('Alphard', 'AlpXhard', True), ('AlpXhard', 'Alphard', True), ('Abcdef', 'Abxyef', False)])
def test_one_edit_short_names_and_insertions(first, second, expected):
    from gwt_check.act_duplicates import _one_edit
    assert _one_edit(first, second) == expected


@pytest.mark.parametrize('change', ['empty-given', 'empty-surnames', 'extra-surname', 'empty-surname', 'alias'])
def test_primary_identity_requires_complete_matching_name(change):
    data = fixture()
    p = data['people'][-1]
    name = p['primary_name']
    if change == 'empty-given':
        name['first_name'] = ''
    elif change == 'empty-surnames':
        name['surname_list'] = []
    elif change == 'extra-surname':
        name['surname_list'].append({'surname': 'Inventedextra'})
    elif change == 'empty-surname':
        for person in data['people'][-2:]:
            person['primary_name']['surname_list'][0]['surname'] = ''
    else:
        p['alternate_names'] = [copy.deepcopy(name)]
        name['first_name'] = 'Different'
    assert hits(data) == []


def test_name_folding_and_unknown_gender_are_compatible():
    data = fixture()
    data['people'][-1]['primary_name']['first_name'] = '  ÍNVENTED  '
    data['people'][-1]['primary_name']['surname_list'][0]['surname'] = ' ÁLPHARD '
    data['people'][-1]['gender'] = 2
    assert len(hits(data)) == 1


@pytest.mark.parametrize('change', ['missing-event', 'missing-citation', 'blank-page', 'numeric-page',
                                   'absent-date', 'short-date', 'unknown-place'])
def test_missing_or_incomplete_evidence_is_quiet(change):
    data = fixture()
    event = data['events'][-1]
    if change == 'missing-event':
        data['people'][-1]['event_ref_list'][0]['ref'] = 'missing'
    elif change == 'missing-citation':
        event['citation_list'] = ['missing']
    elif change == 'blank-page':
        data['citations'][0]['page'] = '   '
    elif change == 'numeric-page':
        data['citations'][0]['page'] = 123
    elif change == 'absent-date':
        event['date'] = None
    elif change == 'short-date':
        event['date']['dateval'] = [1, 1, 1800]
    else:
        for e in data['events'][-2:]:
            e['place'] = 'missing'
    assert hits(data) == []


@pytest.mark.parametrize('value', [None, [], {}, {'dateval': (1, 1, 1800, False)},
                                  {'dateval': [1, 1]}, {'dateval': [0, 0, 0, False]}])
def test_local_date_key_rejects_incomplete_values(value):
    from gwt_check.act_duplicates import _date_key
    assert _date_key(value) is None


def test_local_date_key_keeps_every_stored_qualifier():
    from gwt_check.act_duplicates import _date_key
    a = {'dateval': [1, 1, 1800, False], 'text': 'invented qualifier'}
    assert _date_key(a) == json.dumps(a, sort_keys=True, ensure_ascii=False)
    assert _date_key(a) != _date_key({**a, 'text': 'another invented qualifier'})
    data = fixture()
    data['events'][-1]['date']['text'] = 'invented qualifier'
    assert hits(data) == []


def test_unknown_gender_does_not_override_a_known_conflict():
    data = fixture()
    data['people'][-2]['gender'] = 2
    data['people'][-1]['gender'] = 0
    assert len(hits(data)) == 1


def test_repeated_citation_and_missing_ids_are_deterministic():
    data = fixture()
    for p, e in zip(data['people'][-2:], data['events'][-2:]):
        p.pop('gramps_id')
        e['citation_list'] *= 2
    [row] = hits(data)
    assert row['key'] == 'hI910,hI911'
    data['people'].reverse()
    assert hits(data) == [row]


@pytest.mark.parametrize('field,value', [('modifier', 1), ('quality', 1), ('calendar', 1),
    ('newyear', 1), ('dateval', [0, 1, 1800, False]), ('dateval', [1, 1, 1800, True])])
def test_shared_uncertainty_is_not_exact_evidence(field, value):
    data = fixture()
    for event in data['events'][-2:]:
        event['date'][field] = value
    assert hits(data) == []


def test_multiple_shared_acts_report_each_person_pair_once():
    data = fixture()
    for i, person in enumerate(data['people'][-2:]):
        selftest._event(data, person, f'E{990+i}', 'Burial', (1860, 2, 3))
        data['events'][-1]['citation_list'] = ['hC0001']
    assert len(hits(data)) == 1


@pytest.mark.parametrize('format', ['snapshot', 'gzip'])
def test_cli_offline_snapshot_and_compressed_dump(tmp_path, capsys, format):
    from gwt_check.tree import APPLY_CLASSES, APPLY_SNAPSHOT
    data = fixture()
    if format == 'snapshot':
        payload = {'format': APPLY_SNAPSHOT,
                   'objects': {cls: data[kind] for cls, kind in APPLY_CLASSES.items()}}
        path = tmp_path / 'invented-snapshot.json'
        path.write_text(json.dumps(payload))
    else:
        import gzip
        path = tmp_path / 'invented-dump.json.gz'
        path.write_bytes(gzip.compress(json.dumps({'objects': data}).encode()))
    assert main(['same-act', str(path), '--json']) == 0
    assert json.loads(capsys.readouterr().out) == same_act_report(Tree(data))


def test_cli_live_bad_input_retains_existing_error_exit(monkeypatch, capsys):
    import gwt_check.cli as cli
    def unavailable(args):
        raise BadInput('invented read failure')
    monkeypatch.setattr(cli, 'live_dump', unavailable)
    assert main(['same-act']) == 2
    assert 'invented read failure' in capsys.readouterr().err
