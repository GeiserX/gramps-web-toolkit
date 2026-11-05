import datetime, io, json, os, subprocess, sys, tempfile, unittest
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
        # Invented sources these tests log to, as a user's own registry file extends the shipped one.
        reg = os.path.join(self.dir.name, 'my-sources.tsv')
        with open(reg, 'w', encoding='utf-8') as f:
            f.write(''.join('%s\t\tXX\tindex\t\t\n' % s for s in ('s', 'archive', 'kirkebog', 'metryki')))
        self.env = os.environ.get('GWT_LEDGER_SOURCES')
        os.environ['GWT_LEDGER_SOURCES'] = reg

    def tearDown(self):
        if self.env is None:
            os.environ.pop('GWT_LEDGER_SOURCES', None)
        else:
            os.environ['GWT_LEDGER_SOURCES'] = self.env
        self.dir.cleanup()

    def add(self, person='Ana Peiro Toledo', source='FamilySearch', query='Ana Peiro bautismo 1850-1860',
            result='none', agent='lane-1'):
        return run('--ledger', self.path, 'add', '--person', person, '--label', person, '--source', source,
                   '--query', query, '--result', result, '--agent', agent, '--control-ok', 'run-1')

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
                                 '--source', 'FamilySearch', '--query', query, '--result', 'none', '--control-ok', 'run-1',
                                 '--agent', agent)[0], 0)
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
                '--query', 'q', '--result', 'none', '--control-ok', 'run-1', '--agent', '')
        self.assertIn('add needs --agent', err.getvalue())

    def test_cli_launcher(self):
        self.add()
        p = subprocess.run([sys.executable, CLI, '--ledger', self.path, 'check', '--query', 'ana peiro bautismo 1850 1860'],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        env = dict(os.environ, GWT_LEDGER=self.path)
        p = subprocess.run([sys.executable, CLI, 'stats'], capture_output=True, text=True, env=env)
        self.assertIn('rows\t1', p.stdout)

    def test_blocked_and_denied_rows_are_listed_but_not_done(self):
        # A search that never ran is not a negative: check prints the row and still answers "not done".
        for result in ('blocked', 'denied'):
            self.path = os.path.join(self.dir.name, result + '.tsv')
            self.add(person='Ana Peiro', query='registro civil birth act', result=result)
            argv = ('--ledger', self.path, 'check', '--person', 'ana-peiro', '--query', 'registro civil birth act')
            code, out = run(*argv)
            self.assertEqual(code, 1, result)
            self.assertIn(result, out)
            self.assertEqual(run(*argv, '--any-result'), (0, out))  # the old meaning: any matching row
            self.add(person='Ana Peiro', query='registro civil birth act', result='none')
            self.assertEqual(run(*argv)[0], 0, result)

    def test_a_row_whose_next_date_has_arrived_is_not_done(self):
        for next_date, want in (('2000-01-01', 1), (datetime.date.today().isoformat(), 1), ('2999-01-01', 0)):
            self.path = os.path.join(self.dir.name, next_date + '.tsv')
            run('--ledger', self.path, 'add', '--person', 'ana', '--source', 'fs', '--query', 'q uno',
                '--result', 'none', '--control-ok', 'run-1', '--next-date', next_date)
            argv = ('--ledger', self.path, 'check', '--query', 'q uno')
            self.assertEqual(run(*argv)[0], want, next_date)
            self.assertEqual(run(*argv, '--any-result')[0], 0, next_date)

    def test_queries_in_other_scripts_keep_their_letters(self):
        self.assertEqual(L.normalize('Иван Петров, 1880'), 'иван петров 1880')
        self.assertEqual(L.normalize('Ørsted'), 'ørsted')
        self.assertEqual(L.normalize('Ἀλέξανδρος'), 'αλεξανδροσ')  # casefold folds the final sigma
        self.assertEqual(L.normalize('李 小龍'), '李 小龍')
        self.assertEqual(L.normalize('snake_case'), 'snake case')  # underscore stays a separator, as before
        run('--ledger', self.path, 'add', '--person', 'Hans Ørsted', '--source', 'kirkebog', '--query', 'Ørsted 1777',
            '--result', 'none', '--control-ok', 'run-1')
        row = L.read_rows(self.path)[0]
        self.assertEqual((row['person_key'], row['query']), ('hans-ørsted', 'ørsted 1777'))
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'Hans Ørsted', '--query', 'Ørsted 1777')[0], 0)

    def test_two_different_cyrillic_names_do_not_match(self):
        run('--ledger', self.path, 'add', '--person', 'Ivan Petrov', '--source', 'metryki',
            '--query', 'Иван Петров 1880', '--result', 'none', '--control-ok', 'run-1')
        self.assertEqual(L.read_rows(self.path)[0]['query'], 'иван петров 1880')
        self.assertEqual(run('--ledger', self.path, 'check', '--query', 'Мария Сидорова'), (1, ''))
        self.assertEqual(run('--ledger', self.path, 'check', '--query', 'Мария Сидорова 1880'), (1, ''))
        self.assertEqual(run('--ledger', self.path, 'check', '--query', 'Иван Петров 1880')[0], 0)

    def test_nothing_left_after_normalising_exits_2_never_a_wildcard(self):
        self.add()
        for argv in (('check', '--query', '!!! ...'), ('check', '--person', '«»', '--query', 'Ana Peiro'),
                     ('list', '--person', '...'),
                     ('add', '--person', '?', '--source', 's', '--query', 'q', '--result', 'none', '--control-ok', 'run-1'),
                     ('add', '--person', 'x', '--source', 's', '--query', '—', '--result', 'none', '--control-ok', 'run-1')):
            err = io.StringIO()
            with self.assertRaises(SystemExit) as e, redirect_stderr(err):
                run('--ledger', self.path, *argv)
            self.assertEqual(e.exception.code, 2, argv)
            self.assertIn('nothing left to match after normalising', err.getvalue())
        self.assertEqual(len(L.read_rows(self.path)), 1)

    def test_legacy_ascii_rows_still_match(self):
        # Rows written by the old ASCII-only normalize: lowercase, no accents, words joined by single spaces.
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\t'.join(L.COLUMNS) + '\n')
            f.write('jose-peiro-ruiz\tJose\tfamilysearch\tpeiro jose bautismo 1850 1860\t2026-01-01\tnone\t\ta1\t\trun-1\n')
        for person, query in (('José Peiró Ruiz', 'Peiró, JOSÉ: bautismo 1850-1860'),
                              ('jose-peiro-ruiz', 'bautismo de Jose Peiro 1850 1860')):
            code, out = run('--ledger', self.path, 'check', '--person', person, '--source', 'FamilySearch',
                            '--query', query)
            self.assertEqual(code, 0, query)
        self.assertEqual(run('--ledger', self.path, 'check', '--exact', '--source', 'familysearch',
                             '--query', 'PEIRÓ José bautismo 1850/1860')[0], 0)

    def test_next_date_and_due(self):
        run('--ledger', self.path, 'add', '--person', 'x', '--source', 's', '--query', 'q one', '--result', 'denied',
            '--next-date', '2026-11-14')
        run('--ledger', self.path, 'add', '--person', 'y', '--source', 's', '--query', 'q two', '--result', 'none', '--control-ok', 'run-1')
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
            f.write('\t'.join(L.COLUMNS[:8]) + '\n')
            f.write('ana\tAna\tfs\told query here\t2026-01-01\tnone\t\ta1\n')
        run('--ledger', self.path, 'add', '--person', 'b', '--source', 's', '--query', 'new q', '--result', 'denied',
            '--next-date', '2026-12-01')
        rows = L.read_rows(self.path)
        self.assertEqual([(r['person_key'], r['next_date']) for r in rows], [('ana', ''), ('b', '2026-12-01')])
        self.assertIn('new q', run('--ledger', self.path, 'due', '--on', '2026-12-01')[1])
    def dated(self, date, result, query='Ana Peiro bautismo 1850-1860', source='familysearch'):
        run('--ledger', self.path, 'add', '--person', 'ana', '--source', source, '--query', query,
            '--result', result, '--date', date, '--control-ok', 'run-1')

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

    def test_an_unreadable_ledger_exits_2_not_1(self):
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, 'buscado.tsv')
            os.mkdir(bad)  # exists, but cannot be read as a file
            for cmd in (['check', '--query', 'ana'], ['list', '--person', 'ana'], ['stats']):
                err = io.StringIO()
                with redirect_stderr(err):
                    code, _ = run('--ledger', bad, *cmd)
                self.assertEqual(code, 2, cmd)
                self.assertIn('cannot read the ledger', err.getvalue())
            with open(os.path.join(td, 'latin1.tsv'), 'wb') as f:
                f.write(b'ana\tAna\tfs\tana\t2026-01-01\tnone\t\xe9\n')
            with redirect_stderr(io.StringIO()):
                code, _ = run('--ledger', os.path.join(td, 'latin1.tsv'), 'check', '--query', 'ana')
            self.assertEqual(code, 2)


def write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def fail(*argv):
    """Exit code and stderr of a command expected to stop with an error."""
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        try:
            code = L.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, err.getvalue()


class RegistryTest(unittest.TestCase):
    """Canonical sources, strict results, proof for a none."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'buscado.tsv')
        self.env = os.environ.pop('GWT_LEDGER_SOURCES', None)

    def tearDown(self):
        if self.env is not None:
            os.environ['GWT_LEDGER_SOURCES'] = self.env
        self.dir.cleanup()

    def legacy(self, *rows):
        """A ledger written before the registry: sources as each lane typed them."""
        write(self.path, '\t'.join(L.COLUMNS[:9]) + '\n' + ''.join('\t'.join(r) + '\n' for r in rows))

    def test_an_alias_logged_earlier_answers_a_check_for_the_canonical_id(self):
        self.legacy(('ana-zorvalo', 'Ana Zorvalo', 'fs', 'ana zorvalo bautismo 1850', '2026-01-01', 'hit', 'e.json', 'l1', ''))
        argv = ('--ledger', self.path, 'check', '--person', 'Ana Zorvalo', '--query', 'Ana Zorvalo bautismo 1850')
        self.assertEqual(run(*argv, '--source', 'familysearch')[0], 0)
        self.assertEqual(run(*argv, '--source', 'FamilySearch', '--exact')[0], 0)
        # Positive control: with aliasing off, the same rows do not answer that check.
        rows = L.read_rows(self.path)
        reg = L.load_registry()
        self.assertEqual(len(L.find(rows, 'ana-zorvalo', 'familysearch', 'ana zorvalo bautismo 1850', registry=reg)), 1)
        self.assertEqual(L.find(rows, 'ana-zorvalo', 'familysearch', 'ana zorvalo bautismo 1850'), [])

    def test_aliases_and_task_suffixes_resolve(self):
        reg = L.load_registry()
        for given, want in (('fs', 'familysearch'), ('familysearch-index', 'familysearch'), ('Gaceta', 'gazeta'),
                            ('gazeta-anexos', 'gazeta'), ('fs-verify', 'familysearch-verify'), ('any', 'any'),
                            ('any-sweep', 'any-sweep'), ('no-such-site', None), ('fs-other', None)):
            self.assertEqual(reg.lookup(given), want, given)

    def test_spelling_variants_of_shipped_sources_resolve(self):
        reg = L.load_registry()
        for given, want in (('gaceta-anexos', 'gazeta'), ('gazeta-boe', 'gazeta'), ('Gazeta de Madrid', 'gazeta'),
                            ('familysearch-texto', 'familysearch-fulltext'), ('fs-texto', 'familysearch-fulltext'),
                            ('familysearch-imagen', 'familysearch-images')):
            self.assertEqual(reg.lookup(given), want, given)

    def test_the_reader_ids_are_the_canonical_ids(self):
        reg = L.load_registry()
        for sid in ('boe', 'familysearch', 'genae', 'insee', 'pares'):  # gwt read's own source names
            self.assertEqual(reg.lookup(sid), sid)

    def test_a_prefix_row_covers_every_id_with_that_prefix(self):
        mine = os.path.join(self.dir.name, 'mine.tsv')
        write(mine, 'parish-scan-*\t\tXX\timage\t\t\n')
        reg = L.load_registry(mine)
        self.assertEqual(reg.lookup('parish-scan-12'), 'parish-scan-12')
        self.assertEqual(reg.row('parish-scan-12')['kind'], 'image')
        self.assertIsNone(L.load_registry().lookup('parish-scan-12'))

    def test_add_stores_the_canonical_id(self):
        run('--ledger', self.path, 'add', '--person', 'x', '--source', 'fs', '--query', 'q', '--result', 'hit')
        self.assertEqual(L.read_rows(self.path)[0]['source'], 'familysearch')

    def test_a_source_close_to_a_known_name_exits_2_unless_new_source(self):
        for cmd in (('add', '--result', 'hit'), ('check',)):
            argv = ('--ledger', self.path, cmd[0], '--person', 'x', '--source', 'familysea', '--query', 'q') + cmd[1:]
            code, err = fail(*argv)
            self.assertEqual(code, 2, cmd)
            self.assertIn('not in the registry', err)
            self.assertIn('familysearch', err)  # the close match is offered
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(fail('--ledger', self.path, 'add', '--person', 'x', '--source', 'familysea-verify',
                              '--query', 'q', '--result', 'hit')[0], 2)  # a task suffix does not hide the typo
        self.assertEqual(run('--ledger', self.path, 'add', '--person', 'x', '--source', 'familysea', '--query', 'q',
                             '--result', 'hit', '--new-source')[0], 0)
        self.assertEqual(L.read_rows(self.path)[0]['source'], 'familysea')
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'x', '--source', 'familysea',
                             '--query', 'q', '--new-source')[0], 0)

    def test_a_new_source_far_from_every_known_name_is_kept_with_a_note(self):
        code, err = fail('--ledger', self.path, 'add', '--person', 'x', '--source', 'Parish Book 3', '--query', 'q',
                         '--result', 'hit')
        self.assertEqual(code, 0)
        self.assertIn('not in the registry', err)
        self.assertEqual(L.read_rows(self.path)[0]['source'], 'parish-book-3')
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'x', '--source', 'Parish Book 3',
                             '--query', 'q')[0], 0)

    def test_a_user_registry_extends_the_shipped_one(self):
        mine = os.path.join(self.dir.name, 'mine.tsv')
        write(mine, 'id\taliases\tcountry\tkind\tfrom\tto\nparish-x\tpx\tES\timage\t1600\t1900\n'
                    'familysearch\tfsi\t\tindex\t\t\n')
        reg = L.load_registry(mine)
        self.assertEqual((reg.lookup('px'), reg.lookup('fsi'), reg.lookup('fs')),
                         ('parish-x', 'familysearch', 'familysearch'))
        write(mine, '\ufeffid\taliases\nfamysearch-copy\tfsx\tXX\tindex\nfamilysearch\tfsy\n')  # BOM; kind left empty
        reg = L.load_registry(mine)
        self.assertEqual((reg.lookup('fsx'), reg.lookup('fsy'), reg.row('familysearch')['kind']),
                         ('famysearch-copy', 'familysearch', 'index'))
        write(mine, 'boe-copy\tgaceta\tES\tfulltext\t\t\n')  # an alias that already names another source
        with self.assertRaises(ValueError):
            L.load_registry(mine)
        write(mine, 'bad\t\tES\tmicrofilm\t\t\n')
        with self.assertRaises(ValueError):
            L.load_registry(mine)

    def test_none_without_proof_is_stored_as_unproven_and_is_not_done(self):
        base = ('--ledger', self.path, 'add', '--person', 'x', '--source', 'boe', '--query', 'edicto 1902',
                '--result', 'none')
        code, err = fail(*base)
        self.assertEqual(code, 0)
        self.assertIn('stored as unproven', err)
        self.assertEqual(L.read_rows(self.path)[0]['result'], 'unproven')
        check = ('--ledger', self.path, 'check', '--person', 'x', '--source', 'boe', '--query', 'edicto 1902')
        self.assertEqual(run(*check)[0], 1)
        run(*base, '--control-ok', 'run-2026-11-01')
        self.assertEqual([r['result'] for r in L.read_rows(self.path)], ['unproven', 'none'])
        self.assertEqual(L.read_rows(self.path)[1]['control'], 'run-2026-11-01')
        self.assertEqual(run(*check)[0], 0)

    def test_a_none_logged_before_control_existed_reads_as_unproven(self):
        # An 8-column row from before 1.1.0: a none with no control column proves no more than a new one.
        write(self.path, '\t'.join(L.COLUMNS[:8]) + '\n'
              'ana-zorvalo\tAna Zorvalo\tboe\tana zorvalo\t2026-01-05\tnone\tnotes.md\tlane-a\n')
        check = ('--ledger', self.path, 'check', '--person', 'Ana Zorvalo', '--source', 'boe', '--query', 'ana zorvalo')
        self.assertEqual(run(*check)[0], 1)
        self.assertEqual([r['result'] for r in L.read_rows(self.path)], ['unproven'])
        self.assertIn('\tnone\t', open(self.path, encoding='utf-8').read())  # the stored row stays as written
        with open(self.path, 'a', encoding='utf-8') as f:  # a 1.1.0 row with its control still counts as done
            f.write('ana-zorvalo\tAna Zorvalo\tboe\tana zorvalo\t2026-02-01\tnone\t\tlane-a\t\trun-7\n')
        self.assertEqual(run(*check)[0], 0)

    def test_control_ok_file_must_be_a_passing_control_run(self):
        good, bad = os.path.join(self.dir.name, 'good.txt'), os.path.join(self.dir.name, 'bad.txt')
        write(good, 'control\tcontrol\t--name "Manuel Rodriguez"\t25 read, want at least 10\tok\n')
        write(bad, 'control\tafter\t--name x\t0 read, want at least 10\tSHORT\nvoid\tboe: 1 of 1 canaries\n')
        base = ('--ledger', self.path, 'add', '--person', 'x', '--source', 'boe', '--query', 'q', '--result', 'none')
        code, err = fail(*base, '--control-ok', bad)
        self.assertEqual(code, 2)
        self.assertIn('not a passing control run', err)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(run(*base, '--control-ok', good)[0], 0)
        self.assertEqual(L.read_rows(self.path)[0]['result'], 'none')

    def test_a_control_file_needs_an_ok_line_and_no_short_or_void_line(self):
        ok = 'control\tcontrol\t--name x\t25 read, want at least 10\tok\n'
        base = ('--ledger', self.path, 'add', '--person', 'x', '--source', 'boe', '--query', 'q', '--result', 'none')
        for name, text in (('empty', ''), ('search-only', 'results\t0\n'),
                           ('short', ok + 'control\tafter\t--name x\t0 read, want at least 10\tSHORT\n'),
                           ('void', ok + 'void\tboe: 1 of 1 canaries read less than their minimum\n')):
            ref = os.path.join(self.dir.name, name + '.txt')
            write(ref, text)
            code, err = fail(*base, '--control-ok', ref)
            self.assertEqual(code, 2, name)
            self.assertIn('not a passing control run', err)
        self.assertFalse(os.path.exists(self.path))

    def test_an_empty_source_is_refused(self):
        code, _ = fail('--ledger', self.path, 'add', '--person', 'x', '--source', '-', '--query', 'q', '--result', 'hit')
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.path))

    def test_sources_filters_by_country_and_a_registry_refuses_bad_ids_and_years(self):
        out = run('sources', '--country', 'nl')[1].splitlines()[1:]
        self.assertTrue(out)
        self.assertEqual({line.split('\t')[2] for line in out}, {'NL'})
        mine = os.path.join(self.dir.name, 'mine.tsv')
        write(mine, 'familysearch\tfsy\n')
        self.assertIn('fs', L.load_registry(mine).sources['familysearch']['aliases'].split(','))
        for row in ('Parish X\t\tES\timage\t\t\n', 'parish-x\t\tES\timage\t16th\t\n'):
            write(mine, row)
            with self.assertRaises(ValueError):
                L.load_registry(mine)

    def test_a_control_path_with_a_typo_is_refused_not_stored_as_a_run_id(self):
        base = ('--ledger', self.path, 'add', '--person', 'x', '--source', 'boe', '--query', 'q', '--result', 'none')
        for ref in (os.path.join(self.dir.name, 'boe-contrl.txt'), 'runs/boe-control', 'boe-control.txt', 'no', '-'):
            code, err = fail(*base, '--control-ok', ref)
            self.assertEqual(code, 2, ref)
            self.assertIn('--control-ok', err)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(run(*base, '--control-ok', 'ci-run-4711')[0], 0)  # a run id: no / and no extension
        self.assertEqual(L.read_rows(self.path)[0]['control'], 'ci-run-4711')

    def test_results_outside_the_enum_are_refused_by_add(self):
        for word in ('found', 'negative', 'nothing'):
            code, _ = fail('--ledger', self.path, 'add', '--person', 'x', '--source', 'boe', '--query', 'q',
                           '--result', word)
            self.assertEqual(code, 2, word)
        self.assertFalse(os.path.exists(self.path))

    def test_new_results_count_as_done_or_not(self):
        for result, want in (('out-of-coverage', 0), ('restricted', 1), ('paid', 1), ('in-person', 1),
                             ('unproven', 1)):
            self.path = os.path.join(self.dir.name, result + '.tsv')
            run('--ledger', self.path, 'add', '--person', 'x', '--source', 'boe', '--query', 'q', '--result', result)
            self.assertEqual(run('--ledger', self.path, 'check', '--person', 'x', '--source', 'boe',
                                 '--query', 'q')[0], want, result)

    def test_check_takes_add_flags_and_person_and_source_alone(self):
        run('--ledger', self.path, 'add', '--person', 'x', '--source', 'boe', '--query', 'edicto', '--result', 'hit',
            '--next-date', '2030-01-01')
        argv = ('--ledger', self.path, 'check', '--person', 'x', '--source', 'boe', '--agent', 'lane-2', '--label', 'X')
        self.assertEqual(run(*argv)[0], 0)
        self.assertEqual(run(*argv, '--date', '2030-01-01')[0], 1)  # judged on that day, the row is due again
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'x', '--source', 'pares')[0], 1)
        code, err = fail('--ledger', self.path, 'check', '--person', 'x')
        self.assertEqual(code, 2)
        self.assertIn('--person and --source', err)

    def test_stats_by_source_folds_aliases(self):
        self.legacy(('a', '', 'fs', 'q1', '2026-01-01', 'hit', '', '', ''),
                    ('b', '', 'familysearch', 'q2', '2026-01-01', 'blocked', '', '', ''),
                    ('c', '', 'familysearch', 'q3', '2026-01-01', 'partial', '', '', ''),
                    ('d', '', 'boe', 'q4', '2026-01-01', 'none', '', '', ''))
        out = run('--ledger', self.path, 'stats', '--by', 'source')[1].splitlines()
        self.assertEqual(out[0].split('\t')[:2], ['source', 'rows'])
        self.assertEqual(out[1], 'familysearch\t3\t1\t1\t0\t1\t0.33\t0.33\t0.33')
        self.assertEqual(out[2], 'boe\t1\t0\t0\t0\t0\t0.00\t0.00\t0.00')  # a none with no control is unproven

    def test_import_maps_columns_and_synonyms_and_needs_proof(self):
        src = os.path.join(self.dir.name, 'old-log.tsv')
        write(src, 'who\twhere\twhat\toutcome\twhen\tproof\n'
                   'Ana Zorvalo\tfs\tbautismo 1850\tfound\t2026-01-02\t\n'
                   'Ana Zorvalo\tgaceta\tedicto 1902\tnegative\t2026-01-03\trun-7\n'
                   'Luis Gil\tbvph\tesquela 1930\tnothing\t2026-01-04\t\n')
        code, out = run('--ledger', self.path, 'import-tsv', src, '--agent', 'import',
                        '--map', 'person_key=who,source=where,query=what,result=outcome,date=when,control=proof')
        self.assertEqual(code, 0)
        self.assertIn('imported\t3', out)
        self.assertIn('unproven\t1', out)
        self.assertIn('synonym\tfound->hit\t1', out)
        rows = L.read_rows(self.path)
        self.assertEqual([(r['source'], r['result']) for r in rows],
                         [('familysearch', 'hit'), ('gazeta', 'none'), ('bvph', 'unproven')])
        self.assertEqual(rows[0]['agent'], 'import')

    def test_import_checks_each_control_like_add(self):
        bad = os.path.join(self.dir.name, 'bad.txt')
        write(bad, 'control\tcontrol\t--name x\t0 read, want at least 3\tSHORT\n')
        src = os.path.join(self.dir.name, 'old-log.tsv')
        argv = ('--ledger', self.path, 'import-tsv', src,
                '--map', 'person_key=who,source=where,query=what,result=outcome,control=proof')
        for proof in (bad, 'no', 'runs/missing.txt'):
            write(src, 'who\twhere\twhat\toutcome\tproof\nAna Zorvalo\tboe\tedicto 1902\tnegative\t%s\n' % proof)
            code, err = fail(*argv)
            self.assertEqual(code, 2, proof)
            self.assertIn('line 2: control', err)
        self.assertFalse(os.path.exists(self.path))

    def test_import_control_ok_vouches_for_a_batch_of_old_nones(self):
        good = os.path.join(self.dir.name, 'good.txt')
        write(good, 'control\tcontrol\t--name x\t25 read, want at least 10\tok\n')
        src = os.path.join(self.dir.name, 'old-log.tsv')
        write(src, 'who\twhere\twhat\toutcome\nAna Zorvalo\tboe\tedicto 1902\tnegative\n'
                   'Luis Gil\tboe\tedicto 1903\tfound\n')
        argv = ('--ledger', self.path, 'import-tsv', src, '--map', 'person_key=who,source=where,query=what,result=outcome')
        code, out = run(*argv, '--control-ok', good)
        self.assertEqual(code, 0)
        self.assertIn('unproven\t0', out)
        self.assertEqual([(r['result'], r['control']) for r in L.read_rows(self.path)], [('none', good), ('hit', good)])
        self.assertEqual(run('--ledger', self.path, 'check', '--person', 'ana-zorvalo', '--source', 'boe')[0], 0)
        self.assertEqual(fail(*argv, '--control-ok', 'runs/typo.txt')[0], 2)

    def test_import_refuses_an_empty_query_a_bad_date_and_a_ledger_dir_without_agent(self):
        src = os.path.join(self.dir.name, 'old-log.tsv')
        argv = ('--ledger', self.path, 'import-tsv', src, '--map', 'source=where,query=what,result=outcome,date=when')
        for row in ('boe\t...\thit\t2026-01-01\n', 'boe\tedicto\thit\t2026-13-01\n'):
            write(src, 'where\twhat\toutcome\twhen\n' + row)
            self.assertEqual(fail(*argv)[0], 2, row)
        write(src, 'where\twhat\toutcome\twhen\nboe\tedicto\thit\t2026-01-01\n')
        self.assertEqual(fail('--ledger-dir', os.path.join(self.dir.name, 'lanes'), *argv)[0], 2)
        self.assertFalse(os.path.exists(self.path))

    def test_importing_the_same_file_twice_adds_nothing(self):
        src = os.path.join(self.dir.name, 'old-log.tsv')
        write(src, 'who\twhere\twhat\toutcome\twhen\nAna Zorvalo\tfs\tbautismo 1850\tfound\t2026-01-02\n'
                   'Ana Zorvalo\tfamilysearch\tBautismo, 1850\thit\t2026-01-02\n')
        argv = ('--ledger', self.path, 'import-tsv', src, '--map', 'person_key=who,source=where,query=what,result=outcome,date=when')
        self.assertIn('imported\t1', run(*argv)[1])
        code, out = run(*argv)
        self.assertIn('imported\t0', out)
        self.assertIn('skipped\t2', out)
        self.assertEqual(len(L.read_rows(self.path)), 1)

    def test_import_reads_a_file_with_a_byte_order_mark_and_crlf(self):
        src = os.path.join(self.dir.name, 'excel.tsv')
        with open(src, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('who\twhere\twhat\toutcome\r\nAna Zorvalo\tfs\tbautismo 1850\tfound\r\n')
        code, out = run('--ledger', self.path, 'import-tsv', src, '--map', 'person_key=who,source=where,query=what,result=outcome')
        self.assertEqual(code, 0)
        self.assertEqual([(r['source'], r['result']) for r in L.read_rows(self.path)], [('familysearch', 'hit')])

    def test_import_refuses_the_whole_file_on_one_bad_row(self):
        src = os.path.join(self.dir.name, 'old-log.tsv')
        write(src, 'who\twhere\twhat\toutcome\n'
                   'Ana Zorvalo\tfs\tbautismo 1850\thit\n'
                   'Ana Zorvalo\tfs\tedicto 1902\tmaybe\n')
        argv = ('--ledger', self.path, 'import-tsv', src, '--map', 'person_key=who,source=where,query=what,result=outcome')
        code, err = fail(*argv)
        self.assertEqual(code, 2)
        self.assertIn("line 3: result 'maybe'", err)
        write(src, 'who\twhere\twhat\toutcome\nAna Zorvalo\tfamilysea\tbautismo 1850\thit\n')
        code, err = fail(*argv)
        self.assertEqual(code, 2)
        self.assertIn("line 2: source 'familysea' is not in the registry but close to familysearch", err)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(run(*argv, '--new-source')[0], 0)


class VoidTest(unittest.TestCase):
    setUp, tearDown, add = LedgerTest.setUp, LedgerTest.tearDown, LedgerTest.add

    def void(self, query='Ana Peiro bautismo 1850-1860', *extra):
        return run('--ledger', self.path, *extra, 'void', '--person', 'Ana Peiro Toledo', '--source', 'FamilySearch',
                   '--query', query, '--reason', 'the record was another Ana', '--agent', 'lane-2')

    def check(self, query='Ana Peiro bautismo 1850-1860'):
        return run('--ledger', self.path, 'check', '--person', 'Ana Peiro Toledo', '--source', 'FamilySearch',
                   '--query', query)[0]

    def test_void_retracts_a_search_and_a_later_row_counts_again(self):
        self.add()
        self.assertEqual(self.check(), 0)
        code, out = self.void()
        self.assertEqual(code, 0)
        self.assertIn('\tnone\t', out)  # it prints the rows it voids
        self.assertEqual(self.check(), 1)
        self.add()  # the search is run again and logged: it counts
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.void()[0], 0)  # and a second void retracts that row as well
        self.assertEqual(self.check(), 1)

    def test_void_names_the_rows_it_retracts_and_list_still_shows_them(self):
        self.add()
        ids = [r['id'] for r in L.with_ids(L.read_rows(self.path))]
        self.void()
        rows = L.read_rows(self.path)
        self.assertEqual(rows[-1]['result'], 'void')
        self.assertEqual(rows[-1]['person_label'], 'Ana Peiro Toledo')  # no --label: the retracted row's label
        self.assertEqual(rows[-1]['evidence'], 'voids %s; the record was another Ana' % ids[0])
        out = run('--ledger', self.path, 'list', '--person', 'Ana Peiro Toledo')[1]
        self.assertEqual([l.split('\t')[5] for l in out.splitlines()], ['none', 'void'])

    def test_list_marks_the_voided_row_and_not_its_identical_re_add(self):
        self.add()
        self.void()
        self.add()  # the same line again, logged after the void: it is live
        out = run('--ledger', self.path, 'list', '--person', 'Ana Peiro Toledo')[1]
        ids = [r['id'] for r in L.with_ids(L.read_rows(self.path))]
        cells = [l.split('\t') for l in out.splitlines()]
        self.assertEqual(sorted((c[5], c[10], c[11]) for c in cells),
                         sorted([('none', ids[0], 'voided'), ('void', ids[1], ''), ('none', ids[2], '')]))
        self.assertIn(ids[0], cells[[c[5] for c in cells].index('void')][6])  # the void names the marked row

    def test_import_after_a_void_counts_again(self):
        src = os.path.join(self.dir.name, 'old-log.tsv')
        write(src, 'who\twhere\twhat\toutcome\twhen\nAna Peiro Toledo\tfs\tAna Peiro bautismo 1850-1860\tfound\t'
                   '2026-01-02\n')
        argv = ('--ledger', self.path, 'import-tsv', src, '--map',
                'person_key=who,source=where,query=what,result=outcome,date=when')
        self.assertIn('imported\t1', run(*argv)[1])
        self.void()
        self.assertEqual(self.check(), 1)
        self.assertIn('imported\t1', run(*argv)[1])  # the same row, searched again: not a duplicate of a voided one
        self.assertEqual(self.check(), 0)

    def test_an_edited_voided_line_warns_that_it_counts_again(self):
        self.add()
        self.void()
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.check(), 1)
        self.assertEqual(err.getvalue(), '')
        with open(self.path, encoding='utf-8') as f:
            lines = f.read().splitlines(True)
        lines[1] = lines[1].replace('lane-1', 'lane-9')  # a hand edit of the voided row
        with open(self.path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.check(), 0)
        self.assertIn('1 void id(s) name no row', err.getvalue())

    def test_void_takes_only_the_identical_query_and_needs_a_live_row(self):
        self.add()
        self.add(query='bautismo de Ana Peiró, 1850-1860')  # close enough for check, not the same query
        self.void()
        self.assertEqual(self.check('bautismo de Ana Peiró, 1850-1860'), 0)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.void()[0], 2)  # nothing live left to void
        self.assertIn('no row to void', err.getvalue())
        void_id = L.with_ids(L.read_rows(self.path))[2]['id']
        self.assertIn('already voided by %s' % void_id, err.getvalue())  # a retry, not a typo
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.void('Ana Peiro boda 1870')[0], 2)
        self.assertNotIn('already voided', err.getvalue())
        self.assertEqual(len(L.read_rows(self.path)), 3)

    def test_void_rows_and_what_they_retract_leave_stats_and_due(self):
        run('--ledger', self.path, 'add', '--person', 'Ana Peiro Toledo', '--source', 'FamilySearch', '--query',
            'Ana Peiro bautismo 1850-1860', '--result', 'blocked', '--agent', 'lane-1', '--next-date', '2026-01-01')
        self.add(person='Luis Gil')
        self.assertIn('2026-01-01', run('--ledger', self.path, 'due', '--on', '2026-02-01')[1])
        self.void()
        self.assertEqual(run('--ledger', self.path, 'due', '--on', '2026-02-01'), (0, ''))
        out = run('--ledger', self.path, 'stats')[1]
        self.assertIn('rows\t1\n', out)
        self.assertNotIn('void', out)

    def test_void_with_a_ledger_dir_goes_to_the_lane_file_and_needs_an_agent(self):
        lanes = os.path.join(self.dir.name, 'lanes')
        run('--ledger', self.path, '--ledger-dir', lanes, 'add', '--person', 'Ana Peiro Toledo', '--source', 's',
            '--query', 'q', '--result', 'hit', '--agent', 'lane-1')
        argv = ('--ledger', self.path, '--ledger-dir', lanes, 'void', '--person', 'Ana Peiro Toledo', '--source', 's',
                '--query', 'q', '--reason', 'wrong person')
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run(*argv)
        self.assertEqual(run(*argv, '--agent', 'lane-2')[0], 0)
        self.assertEqual(L.read_rows(os.path.join(lanes, 'lane-2.tsv'))[0]['result'], 'void')
        self.assertEqual(run('--ledger', self.path, '--ledger-dir', lanes, 'check', '--person', 'Ana Peiro Toledo',
                             '--source', 's', '--query', 'q')[0], 1)


def person(handle, gid, first, surname, events=(), parent_family=None):
    return {'handle': handle, 'gramps_id': gid, 'primary_name': {'first_name': first,
            'surname_list': [{'surname': surname}]}, 'event_ref_list': [{'ref': e} for e in events],
            'parent_family_list': [parent_family] if parent_family else []}


def event(handle, kind, year):
    return {'handle': handle, 'type': kind, 'date': {'dateval': [0, 0, year, False], 'sortval': 1}}


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.namesake = False
        self.path, self.dump, self.sources = (os.path.join(d, n) for n in ('buscado.tsv', 'tree.json', 'cols.txt'))
        # An invented family: Ana (I1) is the child of Pedro (I2) and Juana (I3); Luis (I4) is unrelated.
        tree = {'people': [person('h1', 'I1', 'Ana', 'Zorvalo', ['e1'], 'f1'),
                           person('h2', 'I2', 'Pedro', 'Zorvalo', ['e2', 'e3']),
                           person('h3', 'I3', 'Juana', 'Toledo'), person('h4', 'I4', 'Luis', 'Gil')],
                'families': [{'handle': 'f1', 'father_handle': 'h2', 'mother_handle': 'h3',
                              'child_ref_list': [{'ref': 'h1'}]}],
                'events': [event('e1', 'Birth', 1850), event('e2', 'Birth', 1810), event('e3', 'Death', 1880)]}
        self.tree = tree
        write(self.dump, json.dumps({'objects': tree}))
        write(self.sources, 'fs\nmatchid\nboe\n')
        write(self.path, '\t'.join(L.COLUMNS) + '\n' + ''.join('\t'.join(r) + '\n' for r in (
            ('i1', 'Ana', 'familysearch', 'q', '2026-01-01', 'none', '', '', '', 'run-1'),
            ('i1', 'Ana', 'fs', 'q2', '2026-02-01', 'hit', '', '', '', ''),
            ('pedro-zorvalo', 'Pedro', 'boe-es', 'q', '2026-01-01', 'blocked', '', '', '', ''),
            ('i3', 'Juana', 'boe', 'q', '2026-01-01', 'none', '', '', '2000-01-01', 'run-1'))))

    def tearDown(self):
        self.dir.cleanup()

    def matrix(self, *extra):
        code, out = run('--ledger', self.path, 'coverage', '--dump', self.dump, '--sources', self.sources, *extra)
        self.assertEqual(code, 0)
        return [line.split('\t') for line in out.splitlines()]

    def test_matrix_of_people_and_sources(self):
        m = self.matrix('--root', 'I1')
        self.assertEqual(m[0], ['id', 'name', 'familysearch', 'matchid', 'boe'])
        rows = {r[0]: r[2:] for r in m[1:]}
        self.assertEqual(list(rows), ['I1', 'I2', 'I3'])   # the root and its ancestors, not Luis
        self.assertEqual(rows['I1'], ['hit', 'n/a', '-'])  # matchid starts in 1970; born 1850, Ana's life ends by 1960
        self.assertEqual(rows['I2'], ['-', 'n/a', 'blocked'])  # logged under his name, and under an alias of boe
        self.assertEqual(rows['I3'], ['-', '-', 'due'])    # no dates: never n/a; the row's next_date has passed

    def test_a_voided_hit_leaves_the_matrix(self):
        run('--ledger', self.path, 'void', '--person', 'i1', '--source', 'fs', '--query', 'q2', '--reason', 'not her',
            '--agent', 'lane-1')
        rows = {r[0]: r[2:] for r in self.matrix('--root', 'I1')[1:]}
        self.assertEqual(rows['I1'][0], 'none')  # the hit is gone; the older none shows

    def test_a_none_with_no_control_shows_as_unproven(self):
        with open(self.path, 'a', encoding='utf-8') as f:  # a pre-1.1.0 none: no control column filled
            f.write('i4\tLuis\tboe\tq\t2026-01-01\tnone\t\t\t\t\n')
        self.assertEqual({r[0]: r[2:] for r in self.matrix()[1:]}['I4'], ['-', '-', 'unproven'])

    def test_a_key_that_starts_with_the_gramps_id_counts_for_that_person(self):
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write('i4-luis-gil-1901\tLuis\tfs\tq\t2026-01-01\thit\t\t\t\t\n')
        self.assertEqual({r[0]: r[2:] for r in self.matrix()[1:]}['I4'], ['hit', '-', '-'])

    def test_a_key_of_a_suffixed_id_goes_to_that_id_only(self):
        self.tree['people'].append(person('h5', 'I4-b', 'Luis', 'Gil'))  # an import may suffix an id: I4-b
        write(self.dump, json.dumps({'objects': self.tree}))
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write('i4-b-luis-gil\tLuis\tfs\tq\t2026-01-01\thit\t\t\t\t\n')
        rows = {r[0]: r[2:] for r in self.matrix()[1:]}
        self.assertEqual((rows['I4'][0], rows['I4-b'][0]), ('-', 'hit'))

    def test_a_name_two_people_share_matches_neither(self):
        self.tree['people'].append(person('h5', 'I5', 'Pedro', 'Zorvalo'))  # a namesake of I2
        write(self.dump, json.dumps({'objects': self.tree}))
        rows = {r[0]: r[2:] for r in self.matrix()[1:]}
        self.assertEqual((rows['I2'][2], rows['I5'][2]), ('-', '-'))  # the pedro-zorvalo row could be either

    def test_ledger_keys_that_match_nobody_are_counted_on_stderr(self):
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write('someone-else\t\tboe\tq\t2026-01-01\thit\t\t\t\t\n')
        code, err = fail('--ledger', self.path, 'coverage', '--dump', self.dump, '--sources', self.sources)
        self.assertEqual(code, 0)
        self.assertIn('1 of 4 person keys of the ledger match nobody', err)

    def test_a_source_that_ends_before_a_birth_is_n_a_and_an_older_hit_beats_a_newer_none(self):
        self.tree['people'][3]['event_ref_list'] = [{'ref': 'e4'}]
        self.tree['events'].append(event('e4', 'Birth', 1960))  # Luis, born after matricula's records end
        write(self.dump, json.dumps({'objects': self.tree}))
        write(self.sources, 'matricula\nboe\n')
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write('i4\tLuis\tboe\tq\t2026-01-01\thit\t\t\t\t\n'
                    'i4\tLuis\tboe\tq2\t2026-03-01\tnone\t\t\t\trun-2\n')
        self.assertEqual({r[0]: r[2:] for r in self.matrix()[1:]}['I4'], ['n/a', 'hit'])

    def test_whole_tree_without_root(self):
        self.assertEqual([r[0] for r in self.matrix()[1:]], ['I1', 'I2', 'I3', 'I4'])

    def test_unknown_source_or_root_exits_2(self):
        write(self.sources, 'familysea\n')
        self.assertEqual(fail('--ledger', self.path, 'coverage', '--dump', self.dump, '--sources', self.sources)[0], 2)
        write(self.sources, 'fs\n')
        self.assertEqual(fail('--ledger', self.path, 'coverage', '--dump', self.dump, '--sources', self.sources,
                              '--root', 'I99')[0], 2)

    def test_the_shipped_registry_works_as_a_sources_file(self):
        self.sources = next(p for p in L.SHIPPED_REGISTRY if os.path.exists(p))
        m = self.matrix('--root', 'I1')
        self.assertEqual(m[0][:3], ['id', 'name', 'familysearch'])
        self.assertEqual(len(m[0]), 2 + sum(1 for r in L.load_registry().sources if not r.endswith('-*')))
        self.sources = os.path.join(self.dir.name, 'family.tsv')
        write(self.sources, 'id\taliases\nfs\nparish-scan-*\t\tXX\timage\n')  # a family row is no column
        self.assertEqual(self.matrix()[0], ['id', 'name', 'familysearch'])


if __name__ == '__main__':
    unittest.main()
