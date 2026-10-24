import io, json, os, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_leads as L

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gwt-leads')


def person(h, gid, first, sn, events=(), pfam=(), fam=()):
    return {'handle': h, 'gramps_id': gid, 'primary_name': {'first_name': first, 'surname_list': [{'surname': sn}]},
            'event_ref_list': [{'ref': e} for e in events], 'parent_family_list': list(pfam), 'family_list': list(fam)}


def event(h, typ, y=None, place='pl', cites=('c',)):
    return {'handle': h, 'type': {'string': typ}, 'date': {'dateval': [0, 0, y, False] if y else [0, 0, 0, False]},
            'place': place, 'citation_list': list(cites)}


def tree():
    """root -> father (parents unknown) ; root -> mother (parents recorded); one unrelated loner."""
    people = [
        person('r', 'I1', 'Root', 'Test', ['eb1'], ['f1']),
        person('fa', 'I2', 'Father', 'Test', ['eb2'], [], ['f1']),
        person('mo', 'I3', 'Mother', 'Test', ['eb3', 'ed3'], ['f2'], ['f1']),
        person('gm', 'I4', 'Gran', 'Mother', ['eb4', 'ed4'], [], ['f2']),
        person('lo', 'I9', 'Loner', 'Other', [], []),
    ]
    events = [event('eb1', 'Birth', 1990), event('eb2', 'Birth', 1860, cites=()), event('eb3', 'Birth', 1865),
              event('ed3', 'Death', 1930), event('eb4', 'Birth', 1840), event('ed4', 'Death', None, place='')]
    fams = [{'handle': 'f1', 'gramps_id': 'F1', 'father_handle': 'fa', 'mother_handle': 'mo',
             'child_ref_list': [{'ref': 'r'}], 'event_ref_list': []},
            {'handle': 'f2', 'gramps_id': 'F2', 'father_handle': None, 'mother_handle': 'gm',
             'child_ref_list': [{'ref': 'mo'}], 'event_ref_list': []}]
    return {'people': {p['handle']: p for p in people}, 'events': {e['handle']: e for e in events},
            'families': {f['handle']: f for f in fams}}


class LeadsTest(unittest.TestCase):
    def test_generations_count_ancestors_only(self):
        g = L.generations(tree(), 'r')
        self.assertEqual((g['r'], g['fa'], g['gm']), (0, 1, 2))
        self.assertNotIn('lo', g)

    def test_branch_end_ranks_ancestors_above_strangers(self):
        by = {r['person']: r for r in L.leads(tree(), 'I1', ('branch-end',))}
        self.assertEqual(set(by), {'I2', 'I4', 'I9'})  # the root and the mother have parents recorded
        self.assertEqual(by['I2']['priority'], 99)   # father: generation 1
        self.assertEqual(by['I4']['priority'], 98)   # grandmother: generation 2
        self.assertEqual(by['I9']['priority'], 10)   # not an ancestor of the root

    def test_missing_vital_finds_no_death_for_old_births_only(self):
        what = {(r['person'], r['what']) for r in L.leads(tree(), None, ('missing-vital',))}
        self.assertIn(('I2', 'no death event (born 1860)'), what)
        self.assertNotIn(('I1', 'no death event (born 1990)'), what)
        self.assertIn(('I9', 'no birth event'), what)
        self.assertIn(('I4', 'death has no date'), what)
        self.assertIn(('I4', 'death has no place'), what)

    def test_family_with_children_and_no_marriage(self):
        what = [r['what'] for r in L.leads(tree(), None, ('missing-vital',)) if 'no marriage' in r['what']]
        self.assertEqual(len(what), 2)

    def test_uncited_vital(self):
        rows = L.leads(tree(), None, ('uncited',))
        self.assertEqual([(r['person'], r['what']) for r in rows], [('I2', 'birth has no citation')])

    def test_unknown_root_exits(self):
        with self.assertRaises(L.BadInput):
            L.leads(tree(), 'I404')

    def test_queue_format_is_shell_safe(self):
        r = {'person': 'I2', 'label': "O'Brien Test", 'kind': 'branch-end', 'what': 'no parents recorded',
             'priority': 99, 'generation': 1}
        line = L.as_queue_line(r)
        self.assertTrue(line.startswith('gwt-queue add --person I2 '))
        self.assertIn("'\"'\"'", line)  # the apostrophe is quoted, not left to the shell
        self.assertTrue(line.endswith('--dedupe'))

    def test_cli_reads_a_dump_and_prints_tsv(self):
        d = tree()
        data = {t: list(v.values()) for t, v in d.items()}
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'dump.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            p = subprocess.run([CLI, '--root', 'I1', '--kinds', 'uncited', path], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(p.stdout.startswith('99\tI2\t'))

    def test_gz_dump_loads(self):
        import gzip
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'dump.json.gz')
            with gzip.open(path, 'wt') as f:
                json.dump({'objects': {t: list(v.values()) for t, v in tree().items()}}, f)
            self.assertEqual(len(L.load(path)['people']), 5)

    def test_queue_lines_feed_gwt_queue(self):
        with tempfile.TemporaryDirectory() as td:
            q = os.path.join(td, 'cola.tsv')
            qcli = os.path.join(os.path.dirname(CLI), '..', 'queue', 'gwt-queue')
            for r in L.leads(tree(), 'I1', ('branch-end',)):
                line = L.as_queue_line(r).replace('gwt-queue add', '%s --queue %s add' % (qcli, q), 1)
                self.assertEqual(subprocess.run(line, shell=True, capture_output=True).returncode, 0)
                subprocess.run(line, shell=True, capture_output=True)  # second run: --dedupe, no duplicate
            first = subprocess.run([qcli, '--queue', q, 'claim', '--lane', 'l'], capture_output=True, text=True)
            self.assertTrue(first.stdout.split('\t')[1] == 'i2' and first.stdout.split('\t')[6] == '99')
            rows = subprocess.run([qcli, '--queue', q, 'list'], capture_output=True, text=True).stdout.splitlines()
            self.assertEqual(len(rows), 3)

    def test_queue_groups_gaps_per_person_and_sets_source(self):
        rows = L.leads(tree(), 'I1')
        tasks = L.group_by_person(rows)
        keys = [(t['person'], t['task']) for t in tasks]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLessEqual(len(tasks), len(rows))
        for t in tasks:
            self.assertEqual(t['query'] if 'query' in t else t['label'], t['label'])
            self.assertEqual(t['priority'], max(r['priority'] for r in rows if r['person'] == t['person']))
        multi = [t for t in tasks if len(t['gaps']) > 1]
        if multi:
            self.assertIn('; ', L.as_queue_line(multi[0]))
        line = L.as_queue_line(tasks[0], 'familysearch')
        self.assertIn("--source familysearch", line)
        self.assertNotIn('--source', L.as_queue_line(tasks[0]))

    def test_one_word_names_are_not_queued_unless_asked(self):
        rows = [{'person': 'I9', 'label': 'Antonio', 'kind': 'branch-end', 'what': 'no parents recorded', 'priority': 99},
                {'person': 'I8', 'label': 'Ana Lopez', 'kind': 'branch-end', 'what': 'no parents recorded', 'priority': 98}]
        self.assertEqual([t['person'] for t in L.group_by_person(rows)], ['I8'])
        self.assertEqual([t['person'] for t in L.group_by_person(rows, keep_weak=True)], ['I9', 'I8'])

    # --- the real `gwt-check dump` shape, and honest dates ---

    def real_dump(self, td, mutate=None):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'check'))
        from gwt_check import selftest, tree as T
        objects = selftest.clean_tree()
        if mutate:
            mutate(objects)
        path = os.path.join(td, 'dump.json.gz')
        T.save({'tree': 'synthetic', 'dumped_at': '2026-01-01T00:00:00+0000', 'objects': objects}, path)
        return path

    def test_end_to_end_on_a_dump_written_by_gwt_check(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.real_dump(td)
            T = L.load(path)
            self.assertEqual(len(T['people']), 8)
            p = subprocess.run([CLI, '--root', 'I0001', '--format', 'json', path], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            rows = [json.loads(l) for l in p.stdout.splitlines()]
            self.assertTrue(rows, 'a real dump must produce leads')
            self.assertTrue(any(r['kind'] == 'branch-end' and r['generation'] is not None for r in rows))
            self.assertTrue(all(r['priority'] >= 10 for r in rows))

    def test_a_file_that_is_not_a_dump_exits_2_with_a_message(self):
        with tempfile.TemporaryDirectory() as td:
            for name, body in (('a.json', '{"objects": {"people": []}}'), ('b.json', '[1, 2]'),
                               ('c.json', '{"tree": "x"}'), ('d.json', 'not json')):
                path = os.path.join(td, name)
                with open(path, 'w') as f:
                    f.write(body)
                p = subprocess.run([CLI, path], capture_output=True, text=True)
                self.assertEqual(p.returncode, 2, name)
                self.assertIn('gwt-leads:', p.stderr)
                self.assertEqual(p.stdout, '')

    def test_date_text_without_a_year_is_not_called_no_date(self):
        def mutate(objects):
            e = next(e for e in objects['events'] if e['type'] == 'Birth')
            e['date'] = dict(e['date'], dateval=[0, 0, 0, False], year=0, text='before the war')
        with tempfile.TemporaryDirectory() as td:
            T = L.load(self.real_dump(td, mutate))
            what = [r['what'] for r in L.leads(T, None, ('missing-vital',))]
            self.assertIn('birth has date text without an interpretable year (before the war)', what)
            self.assertNotIn('birth has no date', what)

    def test_a_year_in_the_structured_field_alone_counts_as_dated(self):
        ev = {'date': {'year': 1850, 'dateval': [0, 0, 0, False], 'text': ''}}
        self.assertEqual(L.year(ev), 1850)
        self.assertEqual(L.date_gap({'date': {}}), 'has no date')


if __name__ == '__main__':
    unittest.main()
