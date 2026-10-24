import io, os, subprocess, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_ledger as L

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gwt-ledger')


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = L.main(list(argv))
    return code, out.getvalue()


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'buscado.tsv')

    def tearDown(self):
        self.dir.cleanup()

    def add(self, person='Ana Peiro Toledo', source='FamilySearch', query='Ana Peiro bautismo 1850-1860',
            result='none', agent='lane-1'):
        return run('--ledger', self.path, 'add', '--person', person, '--label', person, '--source', source,
                   '--query', query, '--result', result, '--agent', agent)

    def test_normalize_strips_accents_and_punctuation(self):
        self.assertEqual(L.normalize('  Peiró, JOSÉ «Pepe»  1890-1900 '), 'peiro jose pepe 1890 1900')
        self.assertEqual(L.key('José Pérez'), L.key('jose-perez'))

    def test_check_on_empty_ledger_says_not_done(self):
        code, out = run('--ledger', self.path, 'check', '--person', 'x', '--query', 'anything')
        self.assertEqual((code, out), (1, ''))

    def test_add_then_check_exact_and_fuzzy(self):
        self.add()
        code, out = run('--ledger', self.path, 'check', '--person', 'ana-peiro-toledo',
                        '--source', 'familysearch', '--query', 'Ana Peiro bautismo 1850-1860')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('1.00\tana-peiro-toledo\tAna Peiro Toledo\tfamilysearch\t'))
        # Reworded with accents and a different order still counts as done.
        code, _ = run('--ledger', self.path, 'check', '--person', 'Ana Peiro Toledo',
                      '--source', 'FamilySearch', '--query', 'bautismo de Ana Peiró, 1850-1860')
        self.assertEqual(code, 0)

    def test_check_is_scoped_by_person_and_source(self):
        self.add()
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'Luis Gil',
                             '--query', 'Ana Peiro bautismo 1850-1860')[0], 1)
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'Ana Peiro Toledo', '--source',
                             'gaceta', '--query', 'Ana Peiro bautismo 1850-1860')[0], 1)
        # A different search on the same person and source is not done.
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'Ana Peiro Toledo', '--source',
                             'familysearch', '--query', 'defunciones Toledo 1900-1910 viuda')[0], 1)
        # Without --person, the check looks across everyone.
        self.assertEqual(run('--ledger', self.path, 'check', '--query', 'Ana Peiro bautismo 1850 1860')[0], 0)

    def test_short_query_is_covered_by_a_longer_logged_one(self):
        self.add(query='Villanueva del Campo bautismos 1801-1850 libro 3 Ana Peiro hija de Pedro Peiro y Juana Toledo')
        code, _ = run('--ledger', self.path, 'check', '--person', 'Ana Peiro Toledo',
                      '--query', 'Villanueva del Campo bautismos 1801-1850')
        self.assertEqual(code, 0)

    def test_a_different_name_after_a_shared_prefix_is_not_done(self):
        # Two searches of one archive catalogue share the long prefix and differ in one surname.
        # The old scoring matched them (4 of 5 words covered, character ratio over 0.8) and the
        # second search was skipped as already done.
        self.add(source='archive', query='Real Chancilleria de Granada: Peiro Villanueva')
        for q in ('Real Chancilleria de Granada: Toledo', 'Real Chancilleria de Granada: Peiro Campo',
                  'Ana Peiro bautismo 1850-1870'):
            self.assertEqual(run('--ledger', self.path, 'check', '--person', 'Ana Peiro Toledo', '--source',
                                 'archive', '--query', q)[0], 1, q)
        # A typo or a dropped particle in the same search still counts as done.
        for q in ('Real Chancilleria Granada: Peiro Villanueba', 'real chancillería de granada peiro villanueva'):
            self.assertEqual(run('--ledger', self.path, 'check', '--person', 'Ana Peiro Toledo', '--source',
                                 'archive', '--query', q)[0], 0, q)

    def test_typo_rule_is_one_edit_and_spares_identifiers(self):
        self.assertTrue(L.words_covered({'peira'}, {'peiro'}))
        self.assertFalse(L.words_covered({'villanuevaa1'}, {'villanueva1'}))
        self.assertFalse(L.words_covered({'a123456'}, {'a123457'}))
        self.assertFalse(L.words_covered({'peiroxx'}, {'peiro'}))
        # One logged word cannot cover two new ones.
        self.assertFalse(L.words_covered({'antonio', 'antonia'}, {'antonio'}))

    def test_tabs_and_newlines_in_values_cannot_break_the_file(self):
        self.add(query='line one\nline\ttwo')
        rows = L.read_rows(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['query'], 'line one line two')
        with open(self.path) as f:
            self.assertEqual(len(f.read().splitlines()), 2)

    def test_invalid_result_is_refused(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            import contextlib
            with contextlib.redirect_stderr(io.StringIO()):
                self.add(result='maybe')
        self.assertFalse(os.path.exists(self.path))

    def test_list_and_stats(self):
        self.add()
        self.add(query='padron 1860', result='hit', agent='lane-2')
        self.add(person='Luis Gil', source='gaceta', query='edicto 1902', result='blocked')
        code, out = run('--ledger', self.path, 'list', '--person', 'ana peiro toledo')
        self.assertEqual((code, len(out.splitlines())), (0, 2))
        code, out = run('--ledger', self.path, 'stats')
        lines = out.splitlines()
        self.assertIn('rows\t3', lines)
        self.assertIn('persons\t2', lines)
        self.assertIn('result\tnone\t1', lines)
        self.assertIn('source\tfamilysearch\t2', lines)

    def test_concurrent_writers_lose_nothing(self):
        # 16 processes x 25 rows each, all appending at once through the CLI.
        n_proc, n_rows = 16, 25
        script = ('import sys; sys.path.insert(0, %r); import gwt_ledger as L\n'
                  'for i in range(%d): L.main(["--ledger", %r, "add", "--person", "p%%s" %% sys.argv[1], '
                  '"--source", "s", "--query", "q %%d " %% i + "x" * 500, "--result", "none"])'
                  % (os.path.dirname(L.__file__), n_rows, self.path))
        procs = [subprocess.Popen([sys.executable, '-c', script, str(k)]) for k in range(n_proc)]
        self.assertEqual([p.wait() for p in procs], [0] * n_proc)
        with open(self.path) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], '\t'.join(L.COLUMNS))
        self.assertEqual(len(lines), 1 + n_proc * n_rows)
        self.assertTrue(all(len(l.split('\t')) == len(L.COLUMNS) for l in lines))

    def test_ledger_dir_writes_one_file_per_lane_and_reads_them_all(self):
        self.add(query='Ana Peiro defuncion 1890', agent='lane-old')  # the shared ledger from before
        lanes = os.path.join(self.dir.name, 'lanes')
        for agent, query in (('Lane A', 'Ana Peiro bautismo 1850'), ('lane-b', 'Ana Peiro boda 1875')):
            self.assertEqual(run('--ledger', self.path, '--ledger-dir', lanes, 'add', '--person', 'Ana Peiro Toledo',
                                 '--source', 'FamilySearch', '--query', query, '--result', 'none', '--agent', agent)[0], 0)
        self.assertEqual(sorted(os.listdir(lanes)), ['lane-a.tsv', 'lane-b.tsv'])
        with open(self.path) as f:
            self.assertEqual(len(f.read().splitlines()), 2)  # the shared file got no new rows
        for query in ('Ana Peiro bautismo 1850', 'Ana Peiro boda 1875', 'Ana Peiro defuncion 1890'):
            code, _ = run('--ledger', self.path, '--ledger-dir', lanes, 'check', '--person', 'Ana Peiro Toledo',
                          '--source', 'FamilySearch', '--query', query)
            self.assertEqual(code, 0, query)  # a row of lane A is seen from lane B, and the old ledger still counts
        self.assertIn('rows\t3', run('--ledger', self.path, '--ledger-dir', lanes, 'stats')[1])
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'Ana Peiro Toledo', '--source', 'FamilySearch',
                             '--query', 'Ana Peiro boda 1875')[0], 1)  # without the dir, the lane files are not read

    def test_ledger_dir_add_needs_an_agent(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            run('--ledger', self.path, '--ledger-dir', self.dir.name, 'add', '--person', 'x', '--source', 's',
                '--query', 'q', '--result', 'none', '--agent', '')
        self.assertIn('add needs --agent', err.getvalue())

    def test_cli_launcher(self):
        self.add()
        p = subprocess.run([sys.executable, CLI, '--ledger', self.path, 'check', '--query', 'ana peiro bautismo 1850 1860'],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        env = dict(os.environ, GWT_LEDGER=self.path)
        p = subprocess.run([sys.executable, CLI, 'stats'], capture_output=True, text=True, env=env)
        self.assertIn('rows\t1', p.stdout)

    def test_denied_is_a_result_and_still_counts_as_done(self):
        self.add(person='Ana Peiro', query='registro civil birth act', result='denied')
        code, out = run('--ledger', self.path, 'check', '--person', 'ana-peiro', '--query', 'registro civil birth act')
        self.assertEqual(code, 0)
        self.assertIn('denied', out)

    def test_next_date_and_due(self):
        run('--ledger', self.path, 'add', '--person', 'x', '--source', 's', '--query', 'q one', '--result', 'denied',
            '--next-date', '2026-11-14')
        run('--ledger', self.path, 'add', '--person', 'y', '--source', 's', '--query', 'q two', '--result', 'none')
        self.assertEqual(run('--ledger', self.path, 'due', '--on', '2026-11-13'), (0, ''))
        code, out = run('--ledger', self.path, 'due', '--on', '2026-11-14')
        self.assertEqual(code, 0)
        self.assertIn('q one', out)
        self.assertNotIn('q two', out)

    def test_bad_dates_are_refused(self):
        for argv in (('add', '--person', 'x', '--source', 's', '--query', 'q', '--result', 'denied',
                      '--next-date', '14/11/2026'), ('due', '--on', '2026-13-01')):
            with self.assertRaises(SystemExit) as e, redirect_stderr(io.StringIO()):
                run('--ledger', self.path, *argv)
            self.assertEqual(e.exception.code, 2)
        self.assertFalse(os.path.exists(self.path))

    def test_eight_column_ledger_reads_and_takes_new_rows_as_is(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\t'.join(L.COLUMNS[:-1]) + '\n')
            f.write('ana\tAna\tfs\told query here\t2026-01-01\tnone\t\ta1\n')
        run('--ledger', self.path, 'add', '--person', 'b', '--source', 's', '--query', 'new q', '--result', 'denied',
            '--next-date', '2026-12-01')
        rows = L.read_rows(self.path)
        self.assertEqual([(r['person_key'], r['next_date']) for r in rows], [('ana', ''), ('b', '2026-12-01')])
        self.assertIn('new q', run('--ledger', self.path, 'due', '--on', '2026-12-01')[1])
    def dated(self, date, result, query='Ana Peiro bautismo 1850-1860', source='familysearch'):
        run('--ledger', self.path, 'add', '--person', 'ana', '--source', source, '--query', query,
            '--result', result, '--date', date)

    def test_equal_scores_list_the_newest_first(self):
        # An old `none` must not hide a newer `hit`: a caller that reads the first row reuses the newest.
        self.dated('2026-01-01', 'none')
        self.dated('2026-06-01', 'hit')
        self.dated('2026-03-01', 'partial')  # backfilled later, but older: the date wins over file order
        self.dated('2026-06-01', 'blocked')  # same day as the hit, logged after it
        code, out = run('--ledger', self.path, 'check', '--source', 'familysearch',
                        '--query', 'Ana Peiro bautismo 1850-1860')
        self.assertEqual(code, 0)
        self.assertEqual([line.split('\t')[6] for line in out.splitlines()], ['blocked', 'hit', 'partial', 'none'])
        # A better score still comes first: a newer row that only matches with a typo goes last.
        self.dated('2027-01-01', 'hit', query='Ana Peiro bautizmo 1850-1860')
        out = run('--ledger', self.path, 'check', '--query', 'Ana Peiro bautismo 1850-1860')[1]
        self.assertEqual(out.splitlines()[-1].split('\t')[6], 'hit')
        self.assertEqual(out.splitlines()[0].split('\t')[6], 'blocked')

    def test_exact_needs_the_same_source_and_the_identical_query(self):
        self.dated('2026-01-01', 'none', query='Ana Peiro bautismo 1850-1860 Toledo')  # fuzzy match only
        self.dated('2026-01-02', 'none', source='gaceta')                             # other source
        self.dated('2026-01-03', 'hit', query='ANA Peiró, bautismo 1850 1860')        # same once normalized
        base = ('--ledger', self.path, 'check', '--source', 'FamilySearch', '--query', 'Ana Peiro bautismo 1850-1860')
        self.assertEqual(len(run(*base)[1].splitlines()), 2)
        code, out = run(*base, '--exact', '--threshold', '0')
        self.assertEqual(code, 0)
        self.assertEqual([line.split('\t')[6] for line in out.splitlines()], ['hit'])
        # A reworded query is done for the fuzzy check, not for --exact.
        reworded = ('--ledger', self.path, 'check', '--source', 'familysearch', '--query', 'bautismo de Ana Peiro 1850-1860')
        self.assertEqual(run(*reworded)[0], 0)
        self.assertEqual(run(*reworded, '--exact'), (1, ''))
        # --exact refuses to run without a source, so it can never match across sites.
        for src in ((), ('--source', ''), ('--source', '!!')):
            with self.assertRaises(SystemExit) as e, redirect_stderr(io.StringIO()):
                run('--ledger', self.path, 'check', *src, '--query', 'Ana Peiro bautismo 1850-1860', '--exact')
            self.assertEqual(e.exception.code, 2)

if __name__ == '__main__':
    unittest.main()
