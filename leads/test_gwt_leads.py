import io, json, os, subprocess, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_leads as L

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, 'gwt-leads')
QCLI = os.path.join(HERE, '..', 'queue', 'gwt-queue')


def person(h, gid, first, sn, events=(), pfam=(), fam=()):
    return {'handle': h, 'gramps_id': gid, 'primary_name': {'first_name': first, 'surname_list': [{'surname': sn}]},
            'event_ref_list': [{'ref': e, 'role': 'Primary'} for e in events], 'parent_family_list': list(pfam),
            'family_list': list(fam)}


def event(h, typ, y=None, place='pl', cites=('c',), **date):
    return {'handle': h, 'type': {'string': typ},
            'date': dict({'dateval': [0, 0, y, False] if y else [0, 0, 0, False]}, **date),
            'place': place, 'citation_list': list(cites)}


def tree():
    """root -> father (parents unknown) ; root -> mother (parents recorded); one unrelated loner."""
    people = [
        person('r', 'I1', 'Root', 'Test', ['eb1', 'ed1'], ['f1']),
        person('fa', 'I2', 'Father', 'Test', ['eb2'], [], ['f1']),
        person('mo', 'I3', 'Mother', 'Test', ['eb3', 'ed3'], ['f2'], ['f1']),
        person('gm', 'I4', 'Gran', 'Mother', ['eb4', 'ed4'], [], ['f2']),
        person('lo', 'I9', 'Loner', 'Other', ['eb9', 'ed9'], []),
    ]
    events = [event('eb1', 'Birth', 1890), event('ed1', 'Death', 1950), event('eb2', 'Birth', 1860, cites=()),
              event('eb3', 'Birth', 1865), event('ed3', 'Death', 1930), event('eb4', 'Birth', 1840),
              event('ed4', 'Death', None, place=''), event('eb9', 'Birth', 1870), event('ed9', 'Death', 1940)]
    fams = [{'handle': 'f1', 'gramps_id': 'F1', 'father_handle': 'fa', 'mother_handle': 'mo',
             'child_ref_list': [{'ref': 'r'}], 'event_ref_list': []},
            {'handle': 'f2', 'gramps_id': 'F2', 'father_handle': None, 'mother_handle': 'gm',
             'child_ref_list': [{'ref': 'mo'}], 'event_ref_list': []}]
    return {'people': {p['handle']: p for p in people}, 'events': {e['handle']: e for e in events},
            'families': {f['handle']: f for f in fams}, 'places': {}}


def rows_of(T, root=None, kinds=L.KINDS, living_after=1925):
    return L.leads(T, root, kinds, living_after)[0]


def what_of(T, kinds=('missing-vital',), living_after=1925):
    return {(r['person'], r['what']) for r in rows_of(T, None, kinds, living_after)}


def as_dump(T):
    return {'objects': {t: list(v.values()) for t, v in T.items()}}


class LeadsTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.td.cleanup()

    def dump(self, T, name='dump.json'):
        path = os.path.join(self.td.name, name)
        with open(path, 'w') as f:
            json.dump(as_dump(T) if 'people' in T and isinstance(T['people'], dict) else T, f)
        return path

    def cli(self, *argv, stdin=None):
        p = subprocess.run([sys.executable, CLI] + list(argv), capture_output=True, text=True, input=stdin)
        return p.returncode, p.stdout, p.stderr

    def queue(self, q, *argv, stdin=None):
        p = subprocess.run([sys.executable, QCLI, '--queue', q] + list(argv), capture_output=True, text=True,
                           input=stdin)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    # --- the gaps ---

    def test_generations_count_ancestors_only(self):
        g = L.generations(tree(), 'r')
        self.assertEqual((g['r'], g['fa'], g['gm']), (0, 1, 2))
        self.assertNotIn('lo', g)

    def test_adoptive_parents_are_not_a_blood_line(self):
        T = tree()
        T['families']['f1']['child_ref_list'] = [{'ref': 'r', 'frel': 'Adopted', 'mrel': {'string': 'Birth'}}]
        self.assertEqual(L.parents(T, T['people']['r']), ['mo'])
        self.assertNotIn('fa', L.generations(T, 'r'))
        T['families']['f1']['child_ref_list'] = [{'ref': 'r', 'frel': 'Stepchild', 'mrel': 'Foster'}]
        self.assertIn(('I1', 'no parents recorded'), what_of(T, ('branch-end',)))

    def test_branch_end_ranks_ancestors_above_strangers(self):
        by = {r['person']: r for r in rows_of(tree(), 'I1', ('branch-end',))}
        self.assertEqual(set(by), {'I2', 'I4', 'I9'})  # the root and the mother have parents recorded
        self.assertEqual(by['I2']['priority'], 99)   # father: generation 1
        self.assertEqual(by['I4']['priority'], 98)   # grandmother: generation 2
        self.assertEqual(by['I9']['priority'], 10)   # not an ancestor of the root

    def test_missing_vital_finds_no_death_for_old_births_only(self):
        what = what_of(tree())
        self.assertIn(('I2', 'no death or burial event (birth 1860)'), what)
        self.assertIn(('I4', 'death has no date'), what)
        self.assertIn(('I4', 'death has no place'), what)
        self.assertFalse([w for w in what if w[0] in ('I1', 'I3', 'I9') and 'death' in w[1]])

    def test_no_death_is_asked_up_to_the_living_cutoff_only(self):
        T = tree()
        T['events']['eb2']['date'] = {'dateval': [0, 0, 1925, False]}
        self.assertIn(('I2', 'no death or burial event (birth 1925)'), what_of(T, living_after=1925))
        T['events']['eb2']['date'] = {'dateval': [0, 0, 1990, False]}
        T['people'].pop('r')  # else his child, born 1890, proves him old
        rows = L.leads(T, None, ('missing-vital',), 1925, hold=False)[0]
        self.assertFalse([r for r in rows if r['person'] == 'I2' and 'no death' in r['what']])

    def test_a_godparent_ref_is_not_the_godparents_own_birth(self):
        T = tree()
        T['people']['gp'] = person('gp', 'I5', 'God', 'Parent')
        T['people']['gp']['event_ref_list'] = [{'ref': 'eb2', 'role': 'Godparent'}, {'ref': 'ed9', 'role': 'Primary'}]
        what = what_of(T)
        self.assertIn(('I5', 'no birth or baptism event'), what)
        self.assertFalse([w for w in what if w[0] == 'I5' and 'death' in w[1]])

    def test_baptism_stands_in_for_birth_and_burial_for_death(self):
        T = tree()
        T['events']['eb2'] = event('eb2', 'Baptism', 1860)
        T['events']['bu2'] = event('bu2', 'Burial', None, place='')
        what = what_of(T)
        self.assertIn(('I2', 'no death or burial event (baptism 1860)'), what)
        self.assertNotIn(('I2', 'no birth or baptism event'), what)
        T['people']['fa']['event_ref_list'].append({'ref': 'bu2', 'role': 'Primary'})
        what = what_of(T)
        self.assertFalse([w for w in what if w[0] == 'I2' and 'no death' in w[1]])
        self.assertIn(('I2', 'burial has no place'), what)

    def test_birth_ref_index_picks_the_birth_the_person_points_at(self):
        T = tree()
        T['events']['eb2b'] = event('eb2b', 'Birth', 1861, place='')
        T['people']['fa']['event_ref_list'].append({'ref': 'eb2b', 'role': 'Primary'})
        T['people']['fa']['birth_ref_index'] = 1
        what = what_of(T)
        self.assertIn(('I2', 'birth has no place'), what)
        self.assertIn(('I2', 'no death or burial event (birth 1861)'), what)

    def test_family_with_children_and_no_marriage(self):
        what = [w for w in what_of(tree()) if 'no marriage' in w[1]]
        self.assertEqual(sorted(what), [('I2', 'family F1 has children but no marriage event'),
                                        ('I4', 'family F2 has children but no marriage event')])

    def test_uncited_vital(self):
        rows = rows_of(tree(), None, ('uncited',))
        self.assertEqual([(r['person'], r['what']) for r in rows], [('I2', 'birth has no citation')])

    def test_unknown_root_exits(self):
        with self.assertRaises(L.BadInput):
            L.leads(tree(), 'I404')

    # --- who is held back ---

    def living_tree(self):
        T = {'people': {}, 'events': {}, 'families': {}, 'places': {}}

        def add(gid, *evs, private=False):
            h = gid.lower()
            T['people'][h] = person(h, gid, 'Ana', 'Lopez', [e['handle'] for e in evs])
            if private:
                T['people'][h]['private'] = True
            for e in evs:
                T['events'][e['handle']] = e
        add('I1', event('b1', 'Birth', 1925))                       # born in the cutoff year: kept
        add('I2', event('b2', 'Birth', 1926))                       # one year later: may be living
        add('I3', event('b3', 'Birth', 1950), event('d3', 'Death'))  # dead, death has no date: kept
        add('I4', event('b4', 'Birth', 1950), event('u4', 'Burial'))  # burial only: dead, kept
        add('I5', event('p5', 'Baptism', 1960))                     # baptism only, recent: held
        add('I6', event('b6', 'Birth', None, text='in the 1970s'))  # text-only date: held
        add('I7')                                                   # nothing known: held
        add('I8', event('b8', 'Birth', 1850), event('d8', 'Death', 1900), private=True)
        add('I9', event('p9', 'Baptism', 1880))                     # old baptism: kept
        return T

    def test_living_rule_at_the_boundary_and_on_thin_records(self):
        rows, held = L.leads(self.living_tree(), living_after=1925)
        self.assertEqual({r['person'] for r in rows}, {'I1', 'I3', 'I4', 'I9'})
        self.assertEqual(held, {'I2': 'living', 'I5': 'living', 'I6': 'living', 'I7': 'living', 'I8': 'private'})
        self.assertIn(('I3', 'death has no date'), {(r['person'], r['what']) for r in rows})

    def test_cli_holds_back_from_every_format_and_names_the_ids(self):
        path = self.dump(self.living_tree())
        for fmt in ('tsv', 'json', 'queue'):
            code, out, err = self.cli(path, '--format', fmt, '--living-after', '1925')
            self.assertEqual(code, 0, err)
            for gid in ('I2', 'I5', 'I6', 'I7', 'I8'):
                self.assertNotIn('"%s"' % gid if fmt != 'tsv' else '\t%s\t' % gid, out, fmt)
            self.assertIn('held back 5 people', err)
            self.assertIn('I2 I5 I6 I7 I8', err)
            self.assertNotIn('Ana', err)  # ids only, never names
        code, out, err = self.cli(path, '--include-living', '--living-after', '1925')
        self.assertIn('\tI2\t', out)
        self.assertNotIn('held back', err)

    def test_a_dated_descendant_keeps_the_undated_direct_line(self):
        T = tree()
        for h in ('fa', 'mo', 'gm'):
            T['people'][h]['event_ref_list'] = []
        rows, held = L.leads(T, 'I1', ('branch-end', 'missing-vital'), 1925)
        self.assertEqual(held, {})  # the root was born in 1890, so everyone above was born before
        what = {(r['person'], r['what']) for r in rows}
        self.assertIn(('I4', 'no death or burial event'), what)
        self.assertIn(('I4', 'no parents recorded'), what)
        T['events']['eb1']['date'] = {'dateval': [0, 0, 1990, False]}
        T['events'].pop('ed1')
        T['people']['r']['event_ref_list'] = [{'ref': 'eb1', 'role': 'Primary'}]
        held = L.leads(T, 'I1', living_after=1925)[1]
        self.assertEqual(set(held), {'I1', 'I2', 'I3', 'I4'})  # nothing below them is old enough

    def test_a_marriage_more_than_the_cutoff_ago_proves_both_spouses_old(self):
        T = tree()
        for h in ('fa', 'mo', 'r'):
            T['people'][h]['event_ref_list'] = []
        T['events']['m1'] = event('m1', 'Marriage', 1910)
        T['families']['f1']['event_ref_list'] = [{'ref': 'm1', 'role': 'Family'}]
        held = L.leads(T, None, living_after=1925)[1]
        self.assertNotIn('I2', held)
        self.assertNotIn('I3', held)
        self.assertIn('I1', held)  # the child of that marriage is not proven old by it

    def test_an_exact_baptism_proves_age_beside_an_about_birth(self):
        T = tree()
        T['people'].pop('r')
        T['events']['eb2']['date'] = {'dateval': [0, 0, 1920, False], 'modifier': 3}  # about 1920: up to 1930
        T['events']['bp2'] = event('bp2', 'Baptism', 1920)
        self.assertIn('I2', L.leads(T, None, living_after=1925)[1])
        T['people']['fa']['event_ref_list'].append({'ref': 'bp2', 'role': 'Primary'})
        self.assertNotIn('I2', L.leads(T, None, living_after=1925)[1])

    def test_only_a_marriage_among_family_events_proves_age(self):
        T = tree()
        for h in ('fa', 'mo', 'r'):
            T['people'][h]['event_ref_list'] = []
        T['events']['dv1'] = event('dv1', 'Divorce', 1910)
        T['families']['f1']['event_ref_list'] = [{'ref': 'dv1', 'role': 'Family'}]
        held = L.leads(T, None, living_after=1925)[1]
        self.assertIn('I2', held)
        self.assertIn('I3', held)

    def test_the_living_test_uses_the_latest_possible_year(self):
        def held_with(**date):
            T = tree()
            T['events']['eb2']['date'] = dict({'dateval': [0, 0, 1900, False, 0, 0, 1950, False]}, **date)
            T['people'].pop('r')
            return 'I2' in L.leads(T, None, living_after=1925)[1]
        self.assertFalse(held_with())                         # plain 1900
        self.assertTrue(held_with(modifier=4))                # between 1900 and 1950
        self.assertTrue(held_with(modifier=2))                # after 1900: no upper bound
        self.assertFalse(held_with(modifier=1))               # before 1900
        self.assertFalse(held_with(modifier=3))               # about 1900: up to 1910
        self.assertEqual(L.latest_year({'date': {'dateval': [0, 0, 1920, False], 'quality': 1}}), 1930)

    def test_a_private_birth_never_shows_its_year_or_text(self):
        T = tree()
        T['events']['eb2']['private'] = True
        what = {w for p, w in what_of(T) if p == 'I2'}
        self.assertIn('no death or burial event', what)
        self.assertFalse([w for w in what if '1860' in w])
        T['events']['eb2']['date'] = {'dateval': [0, 0, 0, False], 'text': 'secret words'}
        what = {w for p, w in what_of(T) if p == 'I2'}  # still proven old by the root, born 1890
        self.assertIn('birth has date text without an interpretable year', what)
        self.assertFalse([w for w in what if 'secret' in w])

    def test_a_dated_baptism_stands_in_for_a_dateless_birth(self):
        T = tree()
        T['people'].pop('r')
        T['events']['eb2']['date'] = {'dateval': [0, 0, 0, False]}
        T['events']['bp2'] = event('bp2', 'Baptism', 1860, place='')
        T['people']['fa']['event_ref_list'].append({'ref': 'bp2', 'role': 'Primary'})
        rows, held = L.leads(T, None, ('missing-vital',), 1925)
        self.assertNotIn('I2', held)
        what = {r['what'] for r in rows if r['person'] == 'I2'}
        self.assertIn('no death or burial event (baptism 1860)', what)
        self.assertIn('birth has no date', what)
        self.assertEqual(L.facts(T, T['people']['fa'])['bapy'], '1860')

    def test_birth_ref_index_ignores_a_ref_that_is_not_primary(self):
        T = tree()
        T['people']['fa']['event_ref_list'] = [{'ref': 'eb9', 'role': 'Witness'}, {'ref': 'eb2', 'role': 'Primary'}]
        T['people']['fa']['birth_ref_index'] = 0
        self.assertEqual(L.birth(T, T['people']['fa'])['handle'], 'eb2')

    def test_living_after_defaults_to_a_hundred_years_ago(self):
        self.assertEqual(L.default_living_after(), __import__('datetime').date.today().year - 100)

    # --- facts ---

    def facts_tree(self):
        T = tree()
        T['places'] = {'par': {'handle': 'par', 'name': {'value': 'San Juan'}, 'placeref_list': [{'ref': 'town'}]},
                       'town': {'handle': 'town', 'name': {'value': 'Villa | Norte'}, 'placeref_list': [{'ref': 'prov'}]},
                       'prov': {'handle': 'prov', 'name': {'value': 'Provincia'}}}
        T['events']['eb2'] = event('eb2', 'Birth', 1860, place='par', cites=(), modifier=1)
        return T

    def test_facts_mark_inexact_years_and_walk_two_place_levels(self):
        T = self.facts_tree()
        self.assertEqual(L.facts(T, T['people']['fa']), {'by': '<1860', 'bp': 'San Juan, Villa | Norte'})
        T['events']['eb2']['date'] = {'dateval': [0, 0, 1860, False], 'modifier': 0, 'quality': 1}
        self.assertEqual(L.facts(T, T['people']['fa'])['by'], '~1860')
        T['events']['eb2']['date'] = {'dateval': [0, 0, 1860, False]}
        self.assertEqual(L.facts(T, T['people']['fa'])['by'], '1860')
        T['events']['eb2']['private'] = True
        self.assertEqual(L.facts(T, T['people']['fa']), {})

    def test_facts_use_the_primary_baptism_and_burial(self):
        T = tree()
        T['events']['eb2'] = event('eb2', 'Baptism', 1860, place='')
        T['events']['bu2'] = event('bu2', 'Burial', 1901, place='')
        T['people']['fa']['event_ref_list'] += [{'ref': 'bu2', 'role': 'Primary'}, {'ref': 'eb3', 'role': 'Witness'}]
        self.assertEqual(L.facts(T, T['people']['fa']), {'bapy': '1860', 'bury': '1901'})

    def test_queue_note_is_exact_and_escaped(self):
        T = self.facts_tree()
        T['events']['ed2'] = event('ed2', 'Death', 1933, place='prov')
        T['people']['fa']['event_ref_list'].append({'ref': 'ed2', 'role': 'Primary'})
        tasks = [L.queue_task(t) for t in L.group_by_person(rows_of(T, 'I1')) if t['person'] == 'I2']
        search = next(t for t in tasks if t['kind'] == 'search')
        self.assertEqual(search['note'], 'facts: bp=San_Juan,_Villa_%7C_Norte by=<1860 dp=Provincia dy=1933 | '
                                         'branch-end: no parents recorded; '
                                         'marriage-missing-F1: family F1 has children but no marriage event')
        self.assertEqual(search['query'], 'Father Test 1860 San Juan')
        verify = next(t for t in tasks if t['kind'] == 'verify')
        self.assertTrue(verify['note'].endswith('| birth-uncited: birth has no citation'))
        self.assertEqual(L.fact_note({'bp': 'a=b;c_d%'}), 'bp=a%3Db%3Bc%5Fd%25')

    # --- queue output ---

    def test_cli_groups_one_search_and_one_verify_per_person(self):
        code, out, err = self.cli(self.dump(tree()), '--root', 'I1', '--format', 'queue')
        self.assertEqual(code, 0, err)
        tasks = [json.loads(l) for l in out.splitlines()]
        father = [(t['kind'], t['dedupe_key']) for t in tasks if t['person'] == 'I2']
        self.assertEqual(father, [('search', 'i2:search:'), ('verify', 'i2:verify:')])
        self.assertEqual([t['priority'] for t in tasks], sorted((t['priority'] for t in tasks), reverse=True))
        code, out, _ = self.cli(self.dump(tree()), '--root', 'I1', '--format', 'queue', '--per-gap', '--source', 'FS')
        per_gap = [json.loads(l) for l in out.splitlines() if '"I2"' in l]
        self.assertEqual(sorted(t['dedupe_key'] for t in per_gap),
                         ['i2:search:fs:branch-end', 'i2:search:fs:death-missing', 'i2:search:fs:marriage-missing-F1',
                          'i2:verify:fs:birth-uncited'])
        self.assertEqual({t['source'] for t in per_gap}, {'FS'})

    def test_limit_keeps_the_highest_priority_tasks(self):
        path = self.dump(tree())
        code, out, _ = self.cli(path, '--root', 'I1', '--format', 'queue', '--limit', '1')
        self.assertEqual([(t['person'], t['kind']) for t in map(json.loads, out.splitlines())], [('I2', 'search')])
        code, out, _ = self.cli(path, '--root', 'I1', '--limit', '2')
        self.assertEqual([l.split('\t')[:2] for l in out.splitlines()], [['99', 'I2'], ['99', 'I2']])
        code, out, _ = self.cli(path, '--root', 'I1')
        prios = [int(l.split('\t')[0]) for l in out.splitlines()]
        self.assertEqual(prios, sorted(prios, reverse=True))
        self.assertLess(prios[-1], prios[0])

    def test_queue_only_flags_are_refused_in_other_formats(self):
        path = self.dump(tree())
        for flag in (['--source', 'fs'], ['--per-gap'], ['--skip-weak']):
            code, _, err = self.cli(path, *flag)
            self.assertEqual(code, 2, flag)
            self.assertIn('only applies to --format queue', err)

    def test_reseeding_never_duplicates_and_refreshes_the_open_task(self):
        q = os.path.join(self.td.name, 'cola.tsv')
        T = tree()
        code, seeds, _ = self.cli(self.dump(T), '--root', 'I1', '--format', 'queue')
        self.queue(q, 'add', '--jsonl', '-', '--dedupe', stdin=seeds)
        first = self.queue(q, 'list').splitlines()
        T['people']['fa']['primary_name']['first_name'] = 'Fathers'  # a name correction between rounds
        code, seeds, _ = self.cli(self.dump(T), '--root', 'I1', '--format', 'queue')
        self.queue(q, 'add', '--jsonl', '-', '--dedupe', stdin=seeds)
        rows = [l.split('\t') for l in self.queue(q, 'list').splitlines()]
        self.assertEqual(len(rows), len(first))
        self.assertIn('Fathers Test', [r[2] for r in rows])
        claim = self.queue(q, 'claim', '--lane', 'l').split('\t')
        self.assertEqual((claim[1], claim[6]), ('i2', '99'))
        self.queue(q, 'done', '--id', claim[0], '--token', claim[16], '--result', 'none')
        self.queue(q, 'add', '--jsonl', '-', '--dedupe', stdin=seeds)
        self.assertEqual(len(self.queue(q, 'list').splitlines()), len(first))  # a done search is not queued again

    def test_skip_weak_drops_thin_names_but_never_an_ancestor(self):
        T = tree()
        T['people']['fa']['primary_name'] = {'first_name': 'Father', 'surname_list': [{'surname': ''}]}
        T['people']['lo']['primary_name'] = {'first_name': 'Juan Bautista', 'surname_list': []}
        self.assertTrue(L.weak_name(T['people']['lo']))
        self.assertFalse(L.weak_name(T['people']['r']))
        path = self.dump(T)
        code, out, err = self.cli(path, '--root', 'I1', '--format', 'queue', '--skip-weak')
        people = {json.loads(l)['person'] for l in out.splitlines()}
        self.assertIn('I2', people)       # weak, but the root's father
        self.assertNotIn('I9', people)    # weak and not an ancestor
        self.assertIn('skipped 1 people with no given name or no surname: I9', err)
        code, out, err = self.cli(path, '--root', 'I1', '--format', 'queue')
        self.assertIn('I9', {json.loads(l)['person'] for l in out.splitlines()})  # opt-in only
        code, out, _ = self.cli(path, '--format', 'json')
        self.assertTrue(all(json.loads(l)['weak'] for l in out.splitlines() if '"I9"' in l))

    def test_tsv_cells_never_split_a_row(self):
        T = tree()
        T['people']['lo']['primary_name']['first_name'] = 'Lo\tner\nX'
        code, out, _ = self.cli(self.dump(T), '--kinds', 'branch-end')
        self.assertTrue(all(len(l.split('\t')) == 5 for l in out.splitlines()))
        self.assertIn('\tLo ner X Other\t', out)

    # --- reading dumps ---

    def test_cli_reads_a_dump_and_prints_tsv(self):
        code, out, err = self.cli(self.dump(tree()), '--root', 'I1', '--kinds', 'uncited')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, '99\tI2\tFather Test\tuncited\tbirth has no citation\n')

    def test_cli_entrypoint_runs(self):
        p = subprocess.run([CLI, self.dump(tree()), '--kinds', 'uncited'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_gz_dump_loads(self):
        import gzip
        path = os.path.join(self.td.name, 'dump.json.gz')
        with gzip.open(path, 'wt') as f:
            json.dump(as_dump(tree()), f)
        self.assertEqual(len(L.load(path)['people']), 5)

    def snapshot(self, T, name='tree.snapshot.json.gz'):
        """T saved the way `gwt-apply` saves a snapshot: gzipped, objects keyed by Gramps class."""
        import gzip
        cls = {'people': 'Person', 'families': 'Family', 'events': 'Event', 'places': 'Place'}
        objects = {cls[t]: list(v.values()) for t, v in T.items()}
        objects.update(Citation=[], Source=[], Note=[], Tag=[])
        path = os.path.join(self.td.name, name)
        with gzip.open(path, 'wt', encoding='utf-8') as f:
            json.dump({'format': 'gwt-apply-snapshot/1', 'tree': 'demo', 'taken': '2026-01-01T00:00:00+00:00',
                       'objects': objects}, f)
        return path

    def test_a_snapshot_gives_the_same_leads_as_a_dump(self):
        T = tree()
        T['places'] = {'pl': {'handle': 'pl', 'name': {'value': 'Exampleton'}}}
        for fmt in ('tsv', 'json', 'queue'):
            want = self.cli(self.dump(T), '--root', 'I1', '--format', fmt)
            got = self.cli(self.snapshot(T), '--root', 'I1', '--format', fmt)
            self.assertEqual(want[0], 0, want[2])
            self.assertEqual(got, want, fmt)
        self.assertEqual(L.load(self.snapshot(T, 's2.gz')), L.load(self.dump(T)))

    def test_a_gzipped_file_without_the_gz_suffix_loads(self):
        import gzip
        path = os.path.join(self.td.name, 'dump.json')
        with gzip.open(path, 'wt') as f:
            json.dump(as_dump(tree()), f)
        self.assertEqual(len(L.load(path)['people']), 5)
        self.assertEqual(len(L.load(self.snapshot(tree(), 'snap.json'))['people']), 5)

    def test_malformed_records_are_rejected_not_filtered(self):
        ok = {'handle': 'p1', 'gramps_id': 'I1'}
        for objects in ({'people': [{}], 'families': [], 'events': []},
                        {'people': [ok, 'x'], 'families': [], 'events': []},
                        {'people': [ok], 'families': [{'gramps_id': 'F1'}], 'events': []},
                        {'people': [ok], 'families': [], 'events': [None]},
                        {'people': [ok], 'families': [], 'events': [], 'places': [{'title': 'x'}]},
                        {'people': [ok], 'families': [], 'events': [], 'places': 'x'}):
            path = self.dump({'objects': objects})
            with self.assertRaises(L.BadInput):
                L.load(path)
            code, _, _ = self.cli(path)
            self.assertEqual(code, 2, objects)

    def test_places_are_optional_and_read_when_present(self):
        base = {'people': [{'handle': 'p1', 'gramps_id': 'I1'}], 'families': [], 'events': []}
        self.assertEqual(L.load(self.dump({'objects': base}))['places'], {})
        base['places'] = [{'handle': 'pl1', 'name': {'value': 'Tours'}}]
        self.assertIn('pl1', L.load(self.dump({'objects': base}, 'd2.json'))['places'])

    def test_a_file_that_is_not_a_dump_exits_2_with_a_message(self):
        for name, body in (('a.json', '{"objects": {"people": []}}'), ('b.json', '[1, 2]'),
                           ('c.json', '{"tree": "x"}'), ('d.json', 'not json')):
            path = os.path.join(self.td.name, name)
            with open(path, 'w') as f:
                f.write(body)
            code, out, err = self.cli(path)
            self.assertEqual(code, 2, name)
            self.assertIn('gwt-leads:', err)
            self.assertEqual(out, '')
            if name in ('b.json', 'c.json'):
                self.assertIn('gwt-check dump', err)
                self.assertIn('gwt-apply snapshot', err)

    # --- the real `gwt-check dump` shape, and honest dates ---

    def real_dump(self, mutate=None):
        sys.path.insert(0, os.path.join(HERE, '..', 'check'))
        from gwt_check import selftest, tree as T
        objects = selftest.clean_tree()
        if mutate:
            mutate(objects)
        path = os.path.join(self.td.name, 'dump.json.gz')
        T.save({'tree': 'synthetic', 'dumped_at': '2026-01-01T00:00:00+0000', 'objects': objects}, path)
        return path

    def test_end_to_end_on_a_dump_written_by_gwt_check(self):
        path = self.real_dump()
        self.assertEqual(len(L.load(path)['people']), 8)
        code, out, err = self.cli('--root', 'I0001', '--format', 'json', '--include-living', path)
        self.assertEqual(code, 0, err)
        rows = [json.loads(l) for l in out.splitlines()]
        self.assertTrue(rows, 'a real dump must produce leads')
        self.assertTrue(any(r['kind'] == 'branch-end' and r['generation'] is not None for r in rows))

    def test_date_text_without_a_year_is_not_called_no_date(self):
        def mutate(objects):
            e = next(e for e in objects['events'] if e['type'] == 'Birth')
            e['date'] = dict(e['date'], dateval=[0, 0, 0, False], year=0, text='before the war')
        T = L.load(self.real_dump(mutate))
        what = [r['what'] for r in L.leads(T, None, ('missing-vital',), hold=False)[0]]
        self.assertIn('birth has date text without an interpretable year (before the war)', what)
        self.assertNotIn('birth has no date', what)

    def test_a_year_in_the_structured_field_alone_counts_as_dated(self):
        ev = {'date': {'year': 1850, 'dateval': [0, 0, 0, False], 'text': ''}}
        self.assertEqual(L.year(ev), 1850)
        self.assertEqual(L.date_gap({'date': {}}), 'has no date')
        self.assertIsNone(L.year({'date': {'modifier': 6, 'dateval': [0, 0, 1850, False]}}))


if __name__ == '__main__':
    unittest.main()
