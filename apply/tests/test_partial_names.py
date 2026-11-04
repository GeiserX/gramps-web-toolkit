"""Invented cases for a person known only by a surname, without guessed placeholders."""
import pytest
from fake_api import FakeGrampsWeb
from gwt_apply.client import Client
from gwt_apply.ops import Applier, validate, stable_handle
from gwt_apply.dupes import preflight


def person(key='surname-only', **fields):
    return {'op':'add_person','key':key,'ruling':'invented-record-review',
            'first_name':'','surname':'Example','gender':'M', **fields}


def client(fake):
    c=Client('https://gw.example','u','p',tree=fake.tree,dry_run=False,session=fake)
    c.login()
    return c


def test_surname_only_person_roundtrip_rerun_and_duplicate():
    fake=FakeGrampsWeb(); c=client(fake); p=person()
    assert validate([p]) == []
    assert not preflight([p],c)
    result=Applier(c,retries=1,retry_wait=0).run([p])
    assert result[0].status=='done'
    stored=fake.store['people'][stable_handle(p['key'])]
    assert stored['primary_name']['first_name']==''
    assert stored['primary_name']['surname_list'][0]['surname']=='Example'
    writes=len(fake.writes())
    assert Applier(c,retries=1,retry_wait=0,verify=True).run([p])[0].status=='skip'
    assert Applier(c,retries=1,retry_wait=0).run([p])[0].status=='skip'
    assert len(fake.writes())==writes
    other=person('another')
    assert preflight([other],c)
    assert Applier(c,retries=1,retry_wait=0).run([other])[0].status=='conflict'


@pytest.mark.parametrize('fields', [
    {'first_name':'','surname':''}, {'first_name':' ','surname':''},
    {'first_name':'','surname':' '}, {'first_name':None}, {'first_name':42},
    {'surname':None}, {'surname':False},
])
def test_reject_unknown_or_malformed_name(fields):
    assert validate([person(**fields)])


def test_reject_missing_name_fields():
    for field in ('first_name','surname'):
        p=person();del p[field]
        assert validate([p])


def test_given_name_only_still_supported():
    assert validate([person(first_name='Ada',surname='')])==[]


def test_surname_only_parent_can_link_without_guessed_given_name():
    fake=FakeGrampsWeb();c=client(fake)
    ops=[person(),person('child',first_name='Ada',gender='F'),
         {'op':'add_family','key':'one-parent','ruling':'invented-record-review',
          'father':'key:surname-only','children':['key:child']}]
    assert validate(ops)==[]
    assert not preflight(ops,c)
    assert [r.status for r in Applier(c,retries=1,retry_wait=0).run(ops)]==['done']*3
    family=next(iter(fake.store['families'].values()))
    assert family['father_handle']==stable_handle('surname-only')
    assert not family.get('mother_handle')
