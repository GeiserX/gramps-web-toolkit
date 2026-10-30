"""Synthetic test: no real people. Run: python3 test_fr_branches.py"""
import json, os, tempfile, csv
import gwt_fr_branches as m

def dump():
    ev = lambda h, y, pl: {'handle': h, 'date': {'dateval': [0, 0, y, False]}, 'place': pl}
    place = lambda h, n, par=None: {'handle': h, 'name': {'value': n}, 'placeref_list': [{'ref': par}] if par else []}
    person = lambda h, gid, fn, sn, evs: {'handle': h, 'gramps_id': gid, 'primary_name': {'first_name': fn, 'surname_list': [{'surname': sn}]},
                                           'event_ref_list': [{'ref': e} for e in evs]}
    return {'objects': {
        'people': [person('p1', 'I1', 'Ana', 'Ejemplo', ['e1']), person('p2', 'I2', 'Bea', 'Modelo', ['e2'])],
        'families': [],
        'events': [ev('e1', 1850, 'l1'), ev('e2', 1990, 'l1')],
        'places': [place('l1', 'Marray (Indre-et-Loire)', 'l2'), place('l2', 'Indre-et-Loire', 'l3'), place('l3', 'Francia')]}}

def test(tmp=None):
    d = tempfile.mkdtemp()
    json.dump(dump(), open(f'{d}/o.json', 'w'))
    with open(f'{d}/l.jsonl', 'w') as f:
        for p in ('I1', 'I2'):
            f.write(json.dumps({'person': p, 'kind': 'branch-end', 'priority': 1, 'what': 'sin padres', 'facts': {}}) + '\n')
    n, s = m.main(f'{d}/o.json', f'{d}/l.jsonl', f'{d}/o.csv', f'{d}/o.md')
    rows = list(csv.DictReader(open(f'{d}/o.csv')))
    assert n == 2 and rows[0]['person'] == 'I1', rows
    assert rows[0]['serie_ad'] == '6NUM2/149/ ; 6NUM8/149/', rows[0]['serie_ad']
    assert 'NO buscar' in rows[1]['buscar'], rows[1]['buscar']      # born 1990: possibly living
    assert rows[0]['portal_archivo_verificado'].startswith('https://archives.touraine.fr/'), rows[0]['portal_archivo_verificado']
    assert '{q}' in m.PORTAL[41] and 'Rech_commune=' in m.PORTAL[41]
    open(f'{d}/e.tsv', 'w').write('person_key\tx\ty\ni1\t\t\n')
    assert m.main(f'{d}/o.json', f'{d}/l.jsonl', f'{d}/o2.csv', f'{d}/o2.md', f'{d}/e.tsv')[0] == 1  # ledger skips I1
    open(f'{d}/none.jsonl', 'w').close()
    assert m.main(f'{d}/o.json', f'{d}/none.jsonl', f'{d}/n.csv', f'{d}/n.md') == (0, 0)  # no leads: no crash
    print('ok')

if __name__ == '__main__':
    test()
