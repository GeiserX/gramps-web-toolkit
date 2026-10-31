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

    def test_id_order_breaks_a_tie_with_the_id(self):
        self.assertEqual(sorted(['I1', 'I01'], key=L.id_order), sorted(['I01', 'I1'], key=L.id_order))

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
            self.assertIn('held back 5 people (1 marked private, 4 who may be living', err)
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


    def test_unsupported_link_kind_lists_links_no_record_supports_and_never_calls_them_wrong(self):
        T = tree()
        links = {'rows': [{'family': 'F1', 'child': 'I1', 'level': 'cited, names neither', 'flags': []},
                          {'family': 'F2', 'child': 'I3', 'level': 'birth record names both', 'flags': []}]}
        rows, _ = L.leads(T, None, ('unsupported-link',), 1925, links=links)
        self.assertEqual({(r['person'], r['gap']) for r in rows}, {('I1', 'link-F1')})
        self.assertTrue(all('not shown wrong' in r['what'] for r in rows))
        self.assertEqual(L.leads(T, None, ('unsupported-link',), 1925)[0], [])  # no report, no rows

    def test_unsupported_link_holds_back_the_possibly_living_like_every_other_kind(self):
        T = tree()
        T['people']['r']['event_ref_list'] = []  # no dates, no death: may be living
        links = {'rows': [{'family': 'F1', 'child': 'I1', 'level': 'cited, names neither', 'flags': []}]}
        rows, held = L.leads(T, None, ('unsupported-link',), 1925, links=links)
        self.assertEqual(rows, [])
        self.assertIn('I1', held)

    def test_cli_needs_links_and_the_kind_together_and_stops_on_a_bad_report(self):
        d = self.dump(tree())
        self.assertEqual(self.cli(d, '--kinds', 'unsupported-link')[0], 2)
        bad = os.path.join(self.td.name, 'bad.json')
        open(bad, 'w').write('[1]')
        rc, _, err = self.cli(d, '--kinds', 'unsupported-link', '--links', bad)
        self.assertEqual(rc, 2)
        self.assertIn('not a gwt-check links report', err)
        good = os.path.join(self.td.name, 'good.json')
        json.dump({'rows': [{'family': 'F1', 'child': 'I1', 'level': 'cited, names neither'}]}, open(good, 'w'))
        rc, out, _ = self.cli(d, '--kinds', 'unsupported-link', '--links', good, '--format', 'queue', '--include-living')
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.splitlines()[0])['person'], 'I1')

    def test_a_links_report_that_does_not_fit_the_dump_stops_instead_of_giving_wrong_or_no_tasks(self):
        T = tree()
        ok = {'family': 'F1', 'child': 'I1', 'level': 'a record names one', 'flags': []}
        for bad in ({'rows': [dict(ok, level='A record names one')]},     # a renamed level: never a silent empty list
                    {'rows': [1]},                                          # a row that is not an object
                    {'rows': [{'child': 'I1', 'level': 'a record names one'}]},  # no family
                    {'rows': [dict(ok, child='I3')]},                       # I3 is not a child of F1 here
                    {'rows': [dict(ok, family='F7')]}):                     # no family F7 in this dump
            with self.assertRaises(L.BadInput, msg=bad):
                L.leads(T, None, ('unsupported-link',), 1925, hold=False, links=bad)
        with self.assertRaisesRegex(L.BadInput, 'another tree or an older dump'):
            L.leads(T, None, ('unsupported-link',), 1925, hold=False, links={'rows': [dict(ok, family='F2')]})
        d = self.dump(T)
        for bad in ({'rows': [1]}, {'rows': [dict(ok, level='a record names one (v2)')]}):
            f = os.path.join(self.td.name, 'bad-links.json')
            json.dump(bad, open(f, 'w'))
            rc, out, err = self.cli(d, '--kinds', 'unsupported-link', '--links', f)
            self.assertEqual((rc, out), (2, ''), err)
            self.assertNotIn('Traceback', err)

    def test_every_level_gwt_check_links_writes_is_accepted(self):
        # a cited link is the first level gwt-check writes; a report with one used to stop the whole run
        self.assertIn('the link itself is cited', L.LINK_NOT_GAPS)
        # read the levels from gwt-check itself, so a level it adds later fails here instead of in a user's run;
        # the mutant runner copies this package alone, without check/ beside it
        if not os.path.isdir(os.path.join(HERE, '..', 'check', 'gwt_check')):
            self.skipTest('gwt-check is not beside this package')
        sys.path.insert(0, os.path.join(HERE, '..', 'check'))
        from gwt_check.links import LEVELS
        self.assertEqual(set(LEVELS), set(L.LINK_GAPS) | set(L.LINK_NOT_GAPS))

    def test_a_gwt_check_links_report_with_a_cited_link_makes_no_task_and_does_not_stop(self):
        def mutate(objects):
            fam = next(f for f in objects['families'] if f['child_ref_list'])
            cit = next(c for c in objects['citations'] if not c.get('private'))
            fam['child_ref_list'][0]['citation_list'] = [cit['handle']]
        if not os.path.isdir(os.path.join(HERE, '..', 'check', 'gwt_check')):
            self.skipTest('gwt-check is not beside this package')
        path = self.real_dump(mutate)
        from gwt_check import tree as CT
        from gwt_check.links import link_report
        rep = link_report(CT.load(path))
        cited = [r for r in rep['rows'] if r['level'] == 'the link itself is cited']
        self.assertTrue(cited, 'the dump must carry a cited link')
        rows, _ = L.leads(L.load(path), None, ('unsupported-link',), 1925, hold=False, links=rep)
        on_cited = {(r['child'], 'link-' + r['family']) for r in cited}
        self.assertFalse([r for r in rows if (r['person'], r['gap']) in on_cited])

    def test_unsupported_link_skips_what_is_supported_not_by_birth_or_listed_by_another_kind(self):
        T = tree()
        def got(*rows):
            return {(r['person'], r['gap']) for r in L.leads(T, None, ('unsupported-link',), 1925, hold=False,
                                                              links={'rows': list(rows)})[0]}
        one = {'family': 'F1', 'child': 'I1', 'level': 'a record names one', 'flags': []}
        self.assertEqual(got(one), {('I1', 'link-F1')})
        # F2 has a mother and no father: the record naming her names every parent the family has
        self.assertEqual(got({'family': 'F2', 'child': 'I3', 'level': 'a record names one', 'flags': []}), set())
        # no cited birth or baptism at all: uncited and missing-vital already list that person
        self.assertEqual(got(dict(one, level='no record names a parent')), set())
        T['families']['f1']['child_ref_list'][0]['frel'] = 'Adopted'  # not a birth link: nothing to prove by a birth record
        self.assertEqual(got(dict(one, level='cited, names neither')), set())

    def test_an_unsupported_link_task_says_what_to_do_carries_the_flags_and_is_a_verify_task(self):
        flag = "another person in the tree bears the father's name"
        links = {'rows': [{'family': 'F1', 'child': 'I1', 'level': 'a record names one', 'flags': [flag]}]}
        rows, _ = L.leads(tree(), None, ('unsupported-link',), 1925, hold=False, links=links)
        self.assertEqual(len(rows), 1)
        self.assertIn('transcribe', rows[0]['what'])
        self.assertIn(flag, rows[0]['what'])
        self.assertEqual(L.queue_task(rows[0])['kind'], 'verify')  # record work, not a search for a new record

    def test_links_alone_turns_the_kind_on(self):
        d = self.dump(tree())
        f = os.path.join(self.td.name, 'links.json')
        json.dump({'rows': [{'family': 'F1', 'child': 'I1', 'level': 'cited, names neither'}]}, open(f, 'w'))
        rc, out, err = self.cli(d, '--links', f, '--kinds', 'branch-end', '--format', 'json', '--include-living')
        self.assertEqual(rc, 0, err)
        self.assertIn('unsupported-link', {json.loads(x)['kind'] for x in out.splitlines()})

    # --- --archives: where each gap's record is kept ---

    def archives_tree(self):
        """I2's birth is in a parish four levels deep in an invented French chain; I3 died in the department;
        I9 was born in another country."""
        T = tree()
        T['places'] = {
            'par': {'handle': 'par', 'name': {'value': 'Saint-Éxemple'}, 'placeref_list': [{'ref': 'vil'}]},
            'vil': {'handle': 'vil', 'name': {'value': 'Villexemple'}, 'placeref_list': [{'ref': 'dep'}]},
            'dep': {'handle': 'dep', 'name': {'value': 'Exempleshire'}, 'placeref_list': [{'ref': 'fr'}]},
            'fr': {'handle': 'fr', 'name': {'value': 'France'}},
            'oth': {'handle': 'oth', 'name': {'value': 'Otherton'}, 'placeref_list': [{'ref': 'els'}]},
            'els': {'handle': 'els', 'name': {'value': 'Otherland'}}}
        T['events']['eb2'] = event('eb2', 'Birth', 1860, place='par', cites=())
        T['events']['ed3'] = event('ed3', 'Death', 1930, place='dep', cites=())
        T['events']['eb9'] = event('eb9', 'Birth', 1870, place='oth', cites=())
        return T

    def archives(self, *rows, header='match\tarchive\tseries\tyears\turl'):
        path = os.path.join(self.td.name, 'archives-%d.tsv' % len(os.listdir(self.td.name)))
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join((header,) + rows) + '\n')
        return path

    def wheres(self, T, path, root=None):
        rows = L.leads(T, root, L.DEFAULT_KINDS, 1925, archives=L.load_archives(path))[0]
        return {(r['person'], r['gap']): r.get('where') for r in rows}

    URL = 'https://archives.example.org/search?commune={place}&year={year}'

    def test_archives_match_a_country_on_the_whole_place_hierarchy_not_the_two_levels_of_bp(self):
        T = self.archives_tree()
        self.assertEqual(L.facts(T, T['people']['fa'])['bp'], 'Saint-Éxemple, Villexemple')  # France is not in bp
        got = self.wheres(T, self.archives('France\tAD Exemple\t3E\t1793-1902\t' + self.URL))
        want = {'archive': 'AD Exemple', 'series': '3E', 'online': True, 'access': 'unknown',
                'url': 'https://archives.example.org/search?commune=Saint-%C3%89xemple&year=1860'}
        self.assertEqual(got[('I2', 'branch-end')], want)
        self.assertEqual(got[('I2', 'birth-uncited')], want)
        # a death gap asks for the death record: its own place and its own year (1930 is outside 1793-1902)
        self.assertEqual(got[('I3', 'death-uncited')]['url'],
                         'https://archives.example.org/search?commune=Exempleshire&year=1930')
        self.assertIs(got[('I3', 'death-uncited')]['online'], False)
        self.assertIsNone(got[('I9', 'birth-uncited')])  # another country: no hint, the gap is still listed
        self.assertIsNone(got[('I4', 'death-place')])  # an event with no place gets no hint
        self.assertIsNone(got[('I2', 'marriage-missing-F1')])  # a marriage gap has no event to place
        T['events']['eb2']['private'] = True
        self.assertIsNone(self.wheres(T, self.archives('France\tA\t\t\t'))[('I2', 'branch-end')])

    def test_archives_the_deepest_level_wins_then_the_first_row(self):
        got = self.wheres(self.archives_tree(), self.archives(
            'France\tNational\t\t\t', 'Exempleshire\tDepartment\t\t\t', 'Villexemple\tTown hall\t\t\t',
            'VILLEXEMPLE\tSecond town row\t\t\t'))
        self.assertEqual(got[('I2', 'branch-end')]['archive'], 'Town hall')
        self.assertEqual(got[('I3', 'death-uncited')]['archive'], 'Department')

    def test_archives_compare_whole_names_folded_never_substrings(self):
        T = self.archives_tree()
        got = self.wheres(T, self.archives('SAINT EXEMPLE\tParish\t\t\t'))
        self.assertEqual(got[('I2', 'branch-end')]['archive'], 'Parish')
        got = self.wheres(T, self.archives('Ville\tA\t\t\t', 'Saint\tB\t\t\t', 'Exemple\tC\t\t\t', 'Franc\tD\t\t\t'))
        self.assertEqual({k: v for k, v in got.items() if v}, {})

    def test_archives_online_counts_an_exact_or_about_year_only(self):
        T = self.archives_tree()
        path = self.archives('France\tA\t\t1600-1650,1793-1902\t' + self.URL)

        def online(**date):
            T['events']['eb2']['date'] = dict({'dateval': [0, 0, date.pop('y', 1860), False]}, **date)
            w = self.wheres(T, path)[('I2', 'branch-end')]
            return w['online'], w['url'].rsplit('=', 1)[1]
        self.assertEqual(online(), (True, '1860'))
        self.assertEqual(online(y=1620), (True, '1620'))
        self.assertEqual(online(y=1700), (False, '1700'))
        self.assertEqual(online(modifier=3), (True, '1860'))  # about
        self.assertEqual(online(quality=1), (True, '1860'))  # estimated
        self.assertEqual(online(modifier=1), (None, ''))  # before: unknown, never a guess
        self.assertEqual(online(modifier=2), (None, ''))  # after
        self.assertEqual(online(modifier=4), (None, ''))  # a range
        self.assertEqual(online(y=None), (None, ''))  # no year

    def test_archives_access_column_is_optional_and_empty_reads_unknown(self):
        T = self.archives_tree()
        got = self.wheres(T, self.archives('France\tA\t\t\t'))
        self.assertEqual(got[('I2', 'branch-end')]['access'], 'unknown')
        got = self.wheres(T, self.archives('France\tA', header='match\tarchive\tseries\tyears\turl\taccess'))
        self.assertEqual(got[('I2', 'branch-end')], {'archive': 'A', 'series': '', 'url': '', 'online': False,
                                                     'access': 'unknown'})  # trailing cells left out read empty
        for value in L.ACCESS + ('',):
            got = self.wheres(T, self.archives('France\tA\t\t\t\t' + value,
                                               header='match\tarchive\tseries\tyears\turl\taccess'))
            self.assertEqual(got[('I2', 'branch-end')]['access'], value or 'unknown')

    def test_a_bad_archives_file_exits_2_and_prints_nothing(self):
        d = self.dump(self.archives_tree())
        head = 'match\tarchive\tseries\tyears\turl'
        bad = {'unknown column': self.archives('France\tA\t\t\t\tx', header=head + '\tnotes'),
               'missing column': self.archives('France\tA\t\t', header='match\tarchive\tyears\turl'),
               'unknown placeholder': self.archives('France\tA\t\t\thttps://x.example/?q={surname}'),
               'placeholder with a format': self.archives('France\tA\t\t\thttps://x.example/?q={place!r}'),
               'unbalanced brace': self.archives('France\tA\t\t\thttps://x.example/?q={place'),
               'unknown access': self.archives('France\tA\t\t\t\tmaybe', header=head + '\taccess'),
               'backwards years': self.archives('France\tA\t\t1902-1793\t'),
               'years as words': self.archives('France\tA\t\tsoon\t'),
               'extra cell': self.archives('France\tA\t\t\t\tx'),
               'empty match': self.archives('\tA\t\t\t'),
               'empty file': self.archives(header=''),
               'missing file': os.path.join(self.td.name, 'nope.tsv')}
        for why, path in bad.items():
            for fmt in ('json', 'queue'):
                rc, out, err = self.cli(d, '--archives', path, '--format', fmt)
                self.assertEqual((rc, out), (2, ''), why)
                self.assertIn(path, err, why)
        rc, out, err = self.cli(d, '--archives', self.archives('Nowhere\tA\t\t\t'), '--format', 'json')
        self.assertEqual(rc, 0, err)  # no row matches: not an error
        self.assertNotIn('"where"', out)

    def test_archives_add_a_where_part_to_the_queue_note_escaped_like_facts(self):
        T = self.archives_tree()
        T['people']['mo']['event_ref_list'][0] = {'ref': 'eb2b', 'role': 'Primary'}
        T['events']['eb2b'] = event('eb2b', 'Birth', 1865, place='par', cites=())
        T['events']['ed3']['place'] = 'oth'
        tasks = {(t['person'], t['kind']): t for t in (L.queue_task(g) for g in L.group_by_person(
            L.leads(T, 'I1', L.DEFAULT_KINDS, 1925, archives=L.load_archives(self.archives(
                'France\tAD Exemple\t3E 412\t1793-1902\t' + self.URL, 'Otherland\tAD_Other\t\t\t'
            )))[0]))}
        note = tasks[('I2', 'search')]['note']
        self.assertEqual(note.split(' | ')[1],
                         'where: access=unknown archive=AD_Exemple online=true series=3E_412 '
                         'url=https://archives.example.org/search?commune%3DSaint-%25C3%2589xemple&year%3D1860')
        self.assertEqual(len(note.split(' | ')), 3)
        # two gaps on the same record give that record's hint once
        group = next(g for g in L.group_by_person(L.leads(T, 'I1', L.DEFAULT_KINDS, 1925, archives=L.load_archives(
            self.archives('France\tAD Exemple\t\t\t')))[0]) if g['person'] == 'I2' and g['task'] == 'search')
        group['gaps'].append(dict(group['gaps'][0], gap='birth-date', what='birth has no date'))
        self.assertEqual(L.queue_task(group)['note'].split(' | ')[1], 'where: access=unknown archive=AD_Exemple online=false')
        # one task of two gaps on two records: both hints, each once, in gap order
        self.assertEqual(tasks[('I3', 'verify')]['note'].split(' | ')[1],
                         'where: access=unknown archive=AD_Exemple online=true series=3E_412 '
                         'url=https://archives.example.org/search?commune%3DSaint-%25C3%2589xemple&year%3D1865; '
                         'access=unknown archive=AD%5FOther online=false')
        self.assertTrue(tasks[('I4', 'search')]['note'].startswith('facts: by=1840 | branch-end: '))

    @unittest.skipUnless(os.path.isfile(os.path.join(HERE, '..', 'examples', 'archives.tsv')), 'no examples folder')
    def test_the_shipped_example_archives_file_loads_with_one_row_per_access_value(self):
        rows = L.load_archives(os.path.join(HERE, '..', 'examples', 'archives.tsv'))
        self.assertEqual({r['access'] for r in rows}, set(L.ACCESS))

    def test_without_archives_json_and_queue_are_byte_identical(self):
        d = self.dump(self.archives_tree())
        matching = self.archives('France\tAD Exemple\t3E\t1793-1902\t' + self.URL, 'Otherland\tB\t\t\t')
        for fmt in ('json', 'queue'):
            base = ['--root', 'I1', '--format', fmt, '--include-living', d]
            rc, plain, err = self.cli(*base)
            self.assertEqual(rc, 0, err)
            self.assertNotIn('where', plain)
            self.assertEqual(self.cli(*base, '--archives', self.archives('Nowhere\tA\t\t\t'))[1], plain)
            rc, hinted, err = self.cli(*base, '--archives', matching)
            self.assertEqual(rc, 0, err)
            self.assertIn('where', hinted)
            stripped = []
            for line in hinted.splitlines():
                obj = json.loads(line)
                obj.pop('where', None)
                obj['note'] = ' | '.join(x for x in obj.get('note', '').split(' | ') if not x.startswith('where: ')) \
                    if 'note' in obj else obj.pop('note', None)
                if obj['note'] is None:
                    del obj['note']
                stripped.append(json.dumps(obj, ensure_ascii=False) + '\n')
            self.assertEqual(''.join(stripped), plain)  # the flag only adds the hint, it changes nothing else


if __name__ == '__main__':
    unittest.main()
