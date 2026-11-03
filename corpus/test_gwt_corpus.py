import io, json, os, shutil, sqlite3, stat, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gwt_corpus as C

FIX = os.path.join(HERE, 'fixtures')


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = C.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        for sub in ('fscache', 'reader', 'books'):
            shutil.copytree(os.path.join(FIX, sub), os.path.join(self.dir, sub))
        self.db = os.path.join(self.dir, 'idx', 'corpus.db')
        self.roots = [os.path.join(self.dir, s) for s in ('fscache', 'reader', 'books')]
        code, out, err = run('update', '--db', self.db, *self.roots)
        self.assertEqual(code, 0, err)

    def tearDown(self):
        self.tmp.cleanup()

    def search(self, *argv):
        return run('search', '--db', self.db, *argv)


class UpdateTest(Base):
    def rows(self):
        db = sqlite3.connect(self.db)
        return db.execute('SELECT source, url, page, sha FROM meta ORDER BY source, url, page').fetchall()

    def test_every_kind_of_file_is_indexed_with_its_pointer(self):
        rows = self.rows()
        sources = sorted({r[0] for r in rows})
        self.assertEqual(sources, ['file', 'fscache', 'reader'])
        urls = {r[1] for r in rows}
        self.assertIn('https://records.example.org/ark:/99999/1:1:AAAA-111', urls)
        self.assertIn('https://index.example.org/search?surname=Ibarrola&page=1', urls)
        # a .txt beside a gwt-fetch manifest gets the URL of its row, one row per page
        self.assertIn(('file', 'https://gazette.example.org/1902/05/04.txt', 1), [r[:3] for r in rows])
        self.assertIn(('file', 'https://gazette.example.org/1902/05/04.txt', 2), [r[:3] for r in rows])
        self.assertTrue(all(len(r[3]) == 64 for r in rows))

    def test_an_image_body_and_a_candidates_file_are_not_indexed(self):
        self.assertNotIn('https://records.example.org/ark:/99999/3:1:IMG-1', {r[1] for r in self.rows()})
        self.assertEqual(sum(r[0] == 'reader' for r in self.rows()), 1)

    def test_a_script_inside_html_is_not_text(self):
        code, out, _ = self.search('Olmedo', '--source', 'fscache')
        self.assertNotIn('news.example.org', out)

    def test_a_second_update_reads_nothing_and_a_gone_file_leaves(self):
        code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertIn('0 new, 0 changed, 0 gone', out)
        os.remove(os.path.join(self.dir, 'reader', 'ibarrola-search-0123456789.json'))
        code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertIn('1 gone', out)
        self.assertEqual(self.search('Ibarrola')[0], 1)

    def test_a_gone_file_under_another_root_stays(self):
        code, out, _ = run('update', '--db', self.db, self.roots[0])
        self.assertIn('0 gone', out)
        self.assertEqual(self.search('Ibarrola')[0], 0)

    def test_a_body_without_its_metadata_waits_for_the_next_update(self):
        d = os.path.join(self.dir, 'fscache', 'aa')
        os.makedirs(d)
        with open(os.path.join(d, 'aa' + '4' * 62 + '.body'), 'w') as f:
            f.write('Martina Escarza Olmedo')
        run('update', '--db', self.db, *self.roots)
        self.assertEqual(self.search('Escarza')[0], 1)
        with open(os.path.join(d, 'aa' + '4' * 62 + '.json'), 'w') as f:
            json.dump({'url': 'https://example.org/late', 'date': '2026-01-11T00:00:00Z'}, f)
        run('update', '--db', self.db, *self.roots)
        self.assertEqual(self.search('Escarza')[0], 0)

    def test_a_changed_file_is_read_again(self):
        path = os.path.join(self.dir, 'books', 'gazette-1902.txt')
        with open(path, 'a') as f:
            f.write('Addenda: Clara Quiroga.\n')
        os.utime(path, (1, 1))
        code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertIn('1 changed', out)
        self.assertEqual(self.search('Clara Quiroga')[0], 0)

    def test_pdf_pages_come_from_pdftotext_one_row_each(self):
        bindir = os.path.join(self.dir, 'bin')
        os.makedirs(bindir)
        tool = os.path.join(bindir, 'pdftotext')
        with open(tool, 'w') as f:
            f.write('#!/bin/sh\nprintf "Primera hoja sin nombres.\\fSegunda hoja: Gregoria Merino Valverde.\\f"\n')
        os.chmod(tool, os.stat(tool).st_mode | stat.S_IEXEC)
        with open(os.path.join(self.dir, 'books', 'book.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 invented')
        with mock.patch.dict(os.environ, {'PATH': bindir + os.pathsep + os.environ['PATH']}):
            run('update', '--db', self.db, *self.roots)
        code, out, _ = self.search('Gregoria Merino')
        self.assertEqual(code, 0)
        self.assertIn('book.pdf p.2', out)

    def pdftotext_stub(self, script):
        bindir = os.path.join(self.dir, 'bin')
        os.makedirs(bindir, exist_ok=True)
        tool = os.path.join(bindir, 'pdftotext')
        with open(tool, 'w') as f:
            f.write('#!/bin/sh\n' + script + '\n')
        os.chmod(tool, os.stat(tool).st_mode | stat.S_IEXEC)
        return {'PATH': bindir + os.pathsep + os.environ['PATH']}

    def test_a_pdf_read_without_pdftotext_is_read_once_it_is_installed(self):
        with open(os.path.join(self.dir, 'books', 'book.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 invented')
        with mock.patch.object(C.shutil, 'which', return_value=None):
            run('update', '--db', self.db, *self.roots)
        env = self.pdftotext_stub('printf "Hoja: Gregoria Merino Valverde.\\f"')
        with mock.patch.dict(os.environ, env):
            code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertIn('1 new', out)
        self.assertEqual(self.search('Gregoria Merino')[0], 0)

    def test_a_pdf_pdftotext_fails_on_is_tried_again(self):
        with open(os.path.join(self.dir, 'books', 'book.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 invented')
        with mock.patch.dict(os.environ, self.pdftotext_stub('exit 1')):
            code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertIn('1 PDFs pdftotext could not read', out)
        with mock.patch.dict(os.environ, self.pdftotext_stub('printf "Gregoria Merino Valverde"')):
            run('update', '--db', self.db, *self.roots)
        self.assertEqual(self.search('Gregoria Merino')[0], 0)

    def test_one_unreadable_file_never_costs_the_run(self):
        bad = os.path.join(self.dir, 'books', 'zz-gone.txt')
        os.symlink(os.path.join(self.dir, 'nowhere.txt'), bad)
        d = os.path.join(self.dir, 'fscache', 'bb')
        os.makedirs(d)
        with open(os.path.join(d, 'bb' + '5' * 62 + '.json'), 'w') as f:
            f.write('[1, 2]')
        with open(os.path.join(d, 'bb' + '5' * 62 + '.body'), 'w') as f:
            f.write('nada')
        fresh = os.path.join(self.dir, 'idx2', 'corpus.db')
        code, out, _ = run('update', '--db', fresh, *self.roots)
        self.assertEqual(code, 0)
        self.assertIn('2 files could not be read', out)
        self.assertEqual(run('search', '--db', fresh, 'jose olmedo')[0], 0)

    def test_a_zero_result_answer_never_matches_the_name_it_was_asked_for(self):
        with open(os.path.join(self.dir, 'reader', 'none-search.json'), 'w') as f:
            json.dump({'url': 'https://index.example.org/search?name=Juana+Perezvilla', 'json': {
                'query': {'name': 'Juana Perezvilla'}, 'response': {'number_found': 0},
                'responseHeader': {'params': {'q': 'Juana Perezvilla'}}}}, f)
        run('update', '--db', self.db, *self.roots)
        self.assertEqual(self.search('Juana Perezvilla')[0], 1)
        self.assertEqual(self.search('Perezvilla')[0], 1)  # nor the name in the request URL

    def test_a_tree_dump_under_a_root_is_not_indexed(self):
        shutil.copy(os.path.join(FIX, 'tree.json'), os.path.join(self.dir, 'books', 'tree-dump.json'))
        run('update', '--db', self.db, *self.roots)
        self.assertEqual(self.search('Rosa Calvo Soto', '--phrase')[0], 1)

    def test_each_entry_of_a_search_answer_keeps_its_own_record_link(self):
        d = os.path.join(self.dir, 'fscache', 'cc')
        os.makedirs(d)
        with open(os.path.join(d, 'cc' + '6' * 62 + '.json'), 'w') as f:
            json.dump({'url': 'https://records.example.org/search?q=Arrieta', 'date': '2026-01-09T00:00:00Z'}, f)
        entries = [{'id': 'E-%d' % i, 'links': {'person': {'href': 'https://records.example.org/ark:/99999/1:1:E-%d' % i}},
                    'content': {'gedcomx': {'persons': [{'names': [{'nameForms': [{'fullText': n}]}]}]}}}
                   for i, n in enumerate(('Matilde Arrieta Goñi', 'Benito Arrieta Sanz'))]
        with open(os.path.join(d, 'cc' + '6' * 62 + '.body'), 'w') as f:
            json.dump({'entries': entries}, f)
        run('update', '--db', self.db, *self.roots)
        code, out, _ = self.search('Benito Arrieta')
        self.assertEqual(code, 0)
        self.assertIn('ark:/99999/1:1:E-1', out)
        self.assertNotIn('E-0', out)

    def test_a_trailing_form_feed_is_not_a_page(self):
        path = os.path.join(self.dir, 'books', 'two.txt')
        with open(path, 'w') as f:
            f.write('uno\fdos\f')
        self.assertEqual(len(C.extract(path, {'urls': {}})), 2)

    def test_a_pdf_without_pdftotext_is_counted_not_silent(self):
        with open(os.path.join(self.dir, 'books', 'book.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 invented')
        with mock.patch.object(C.shutil, 'which', return_value=None):
            code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertIn('1 PDFs not read: pdftotext', out)

    def test_a_scanned_pdf_with_no_text_is_counted(self):
        with open(os.path.join(self.dir, 'books', 'scan.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 invented')
        with mock.patch.dict(os.environ, self.pdftotext_stub('printf "\\f\\f"')):
            code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertIn('1 PDFs have no text layer', out)

    def test_a_pdftotext_that_times_out_is_tried_again(self):
        with open(os.path.join(self.dir, 'books', 'book.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 invented')
        with mock.patch.object(C.shutil, 'which', return_value='/bin/pdftotext'), \
                mock.patch.object(C.subprocess, 'run', side_effect=C.subprocess.TimeoutExpired('pdftotext', 600)):
            code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertEqual(code, 0)
        self.assertIn('1 PDFs pdftotext could not read', out)

    def test_one_update_at_a_time(self):
        import fcntl
        with open(self.db + '.lock', 'w') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # another update is running
            code, _, err = run('update', '--db', self.db, *self.roots)
        self.assertEqual(code, 2)
        self.assertIn('another update', err)

    def test_a_long_first_index_keeps_what_it_read_when_it_stops(self):
        root = os.path.join(self.dir, 'many')
        os.makedirs(root)
        for i in range(1001):
            with open(os.path.join(root, '%04d.txt' % i), 'w') as f:
                f.write('hoja %d' % i)
        real, calls = C.extract, []

        def stop_at_the_last(path, ctx):
            calls.append(path)
            if len(calls) == 1001:
                raise KeyboardInterrupt  # the run is stopped before its final commit
            return real(path, ctx)

        fresh = os.path.join(self.dir, 'idx3', 'corpus.db')
        with mock.patch.object(C, 'extract', stop_at_the_last), self.assertRaises(KeyboardInterrupt):
            run('update', '--db', fresh, root)
        self.assertEqual(sqlite3.connect(fresh).execute('SELECT count(*) FROM files').fetchone()[0], 1000)


class SearchTest(Base):
    def test_accents_and_case_fold(self):
        code, out, _ = self.search('jose olmedo')
        self.assertEqual(code, 0)
        self.assertIn('ark:/99999/1:1:AAAA-111', out)
        self.assertEqual(self.search('LUCIA VALVERDE')[0], 0)

    def test_words_need_each_other_never_or(self):
        # Olmedo is in one file and Merino in another: a search for both finds nothing
        self.assertEqual(self.search('Olmedo Merino')[0], 1)
        self.assertEqual(self.search('Olmedo OR Merino')[0], 1)

    def test_a_plus_in_the_query_stays_and(self):
        code, out, _ = self.search('Olmedo+Merino')
        self.assertEqual(code, 1)
        self.assertEqual(self.search('Olmedo+Quiroga')[0], 0)
        self.assertEqual(self.search('Quiroga+Olmedo')[0], 0)  # two words near each other, not one phrase
        self.assertEqual(self.search('Olmedo "Quiroga')[0], 0)  # a stray quote is a separator, never FTS5 syntax

    def test_words_far_apart_are_not_near(self):
        self.assertEqual(self.search('Lucía Robledal', '--near', '3')[0], 1)
        self.assertEqual(self.search('Lucía Robledal')[0], 0)

    def test_phrase_needs_the_words_in_order(self):
        self.assertEqual(self.search('Quiroga Olmedo')[0], 0)
        self.assertEqual(self.search('Quiroga Olmedo', '--phrase')[0], 1)

    def test_zero_hits_says_not_downloaded_yet(self):
        code, out, _ = self.search('Eusebia Garaioa')
        self.assertEqual(code, 1)
        self.assertIn('not downloaded yet', out)
        self.assertNotIn('does not exist.', out)

    def test_source_filter(self):
        self.assertEqual(self.search('Ibarrola', '--source', 'fscache')[0], 1)
        self.assertEqual(self.search('Ibarrola', '--source', 'reader')[0], 0)

    def test_raw_fts_needs_a_control(self):
        code, _, err = self.search('--fts', 'olmedo OR merino')
        self.assertEqual(code, 2)
        self.assertIn('--control', err)

    def test_raw_fts_with_a_control_that_misses_is_void(self):
        code, out, _ = self.search('--fts', 'olmedo OR merino', '--control', 'garaioa')
        self.assertEqual(code, 3)
        self.assertIn('VOID', out)
        self.assertEqual(self.search('--fts', 'olmedo OR merino', '--control', 'quiroga')[0], 0)

    def test_a_sharp_s_query_matches_the_indexed_word(self):
        with open(os.path.join(self.dir, 'books', 'weiss.txt'), 'w') as f:
            f.write('Taufe von Johann Weiß in Grünfeld.')
        run('update', '--db', self.db, *self.roots)
        self.assertEqual(self.search('Johann Weiß')[0], 0)

    def test_a_limit_below_one_is_refused_and_a_full_limit_says_first(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                C.main(['search', '--db', self.db, 'Olmedo', '--limit', '0'])
        code, out, _ = self.search('Villazul', '--limit', '1')
        self.assertIn('first 1 hits', out)

    def test_no_index_is_bad_input(self):
        self.assertEqual(run('search', '--db', os.path.join(self.dir, 'none.db'), 'x')[0], 2)


class SweepTest(Base):
    def sweep(self, *argv):
        return run('sweep', '--db', self.db, *argv)

    def test_hits_and_negatives_become_ledger_rows(self):
        ledger = os.path.join(self.dir, 'buscado.tsv')
        code, out, err = self.sweep('--names', os.path.join(FIX, 'persons.tsv'), '--control', 'Ana Ibarrola Zubiri',
                                    '--ledger', ledger, '--agent', 'lane-a')
        self.assertEqual(code, 0, err)
        rows = [l.split('\t') for l in open(ledger, encoding='utf-8').read().splitlines()[1:]]
        got = {(r[0], r[3], r[5]) for r in rows}
        self.assertIn(('k-olmedo', '"jose olmedo quiroga"', 'partial'), got)  # a lead to read, never a finding
        self.assertIn(('k-merino', '"tomas merino santos"', 'partial'), got)
        self.assertIn(('k-absent', '"elena zubiri calvo"', 'none'), got)
        self.assertIn(('k-absent', '"zubiri calvo"', 'none'), got)
        self.assertTrue(all(r[2] == 'corpus' and r[7] == 'lane-a' for r in rows))
        self.assertTrue(all(r[9].startswith('corpus-sweep-') for r in rows))  # the controls that passed
        self.assertIn('Tomás Merino (the miller)', out)
        olmedo = next(r for r in rows if r[0] == 'k-olmedo')
        self.assertEqual(olmedo[1], 'José Olmedo Quiroga')  # an empty label falls back to the name
        self.assertTrue(olmedo[6].startswith('https://'), olmedo)  # a lead names where to read it
        nones = [r for r in rows if r[5] == 'none']
        self.assertTrue(all(r[8] == r[4] and r[6] == 'corpus:corpus.db' for r in nones))
        # a corpus miss is due again at once: it never tells gwt-ledger the person was searched
        sys.path.insert(0, os.path.join(HERE, '..', 'ledger'))
        import gwt_ledger
        with redirect_stdout(io.StringIO()):
            self.assertEqual(gwt_ledger.main(['--ledger', ledger, 'check', '--person', 'k-absent',
                                              '--query', 'zubiri calvo']), 1)
        # the same sweep again on the same day adds nothing
        self.sweep('--names', os.path.join(FIX, 'persons.tsv'), '--control', 'Ana Ibarrola Zubiri',
                   '--ledger', ledger, '--agent', 'lane-a')
        self.assertEqual(len(open(ledger, encoding='utf-8').read().splitlines()), len(rows) + 1)

    def test_a_dump_is_swept_with_two_surnames_in_one_field(self):
        code, out, _ = self.sweep('--dump', os.path.join(FIX, 'tree.json'), '--control', 'José Olmedo Quiroga')
        self.assertEqual(code, 0)
        self.assertIn('EX2\tLucía Valverde Ortega\tname\tlucia valverde ortega', out)

    def test_a_missing_control_makes_the_run_void(self):
        ledger = os.path.join(self.dir, 'buscado.tsv')
        code, out, _ = self.sweep('--names', os.path.join(FIX, 'persons.tsv'), '--control', 'Ana Ibarrola Zubiri',
                                  '--control', 'Eusebia Garaioa', '--ledger', ledger)
        self.assertEqual(code, 3)
        self.assertIn('VOID', out)
        self.assertNotIn('k-olmedo', out)  # stopped before searching anyone
        self.assertFalse(os.path.exists(ledger))

    def test_a_control_lost_at_the_end_makes_the_run_void(self):
        ledger = os.path.join(self.dir, 'buscado.tsv')
        real, calls = C.run_query, []

        def flaky(db, expr, source=None, limit=20):
            calls.append(expr)
            if expr == 'body : ("Ana Ibarrola Zubiri")' and len(calls) > 1:
                return []  # the index went away during the run
            return real(db, expr, source, limit)

        with mock.patch.object(C, 'run_query', flaky):
            code, out, _ = self.sweep('--names', os.path.join(FIX, 'persons.tsv'), '--control',
                                      'Ana Ibarrola Zubiri', '--ledger', ledger)
        self.assertEqual(code, 3)
        self.assertIn('after the sweep', out)
        self.assertFalse(os.path.exists(ledger))

    def test_particles_are_not_surnames_and_common_short_names_are_no_leads(self):
        books = os.path.join(self.dir, 'books')
        with open(os.path.join(books, 'torre.txt'), 'w') as f:
            f.write('Se vende la torre del molino de la Torre Vieja.')
        for i in range(8):
            with open(os.path.join(books, 'juan-%d.txt' % i), 'w') as f:
                f.write('Firma Juan García como testigo, hoja %d.' % i)
        run('update', '--db', self.db, *self.roots)
        names = os.path.join(self.dir, 'n.tsv')
        with open(names, 'w', encoding='utf-8') as f:
            f.write('\ufeffkey\tgiven\tsurname\r\nk-torre\tLuis\tde la Torre\r\nk-juan\tJuan\tGarcía\r\n')
        code, out, err = self.sweep('--names', names, '--control', 'Ana Ibarrola Zubiri')
        self.assertNotIn('\tpair\t', out)  # «de la Torre» is one surname: no pair «la torre»
        self.assertNotIn('k-juan', out)  # on 8 pages: too common to be a lead
        self.assertIn('1 too common', err)

    def test_a_short_row_is_bad_input(self):
        names = os.path.join(self.dir, 'n.tsv')
        with open(names, 'w') as f:
            f.write('key\tgiven\tsurname\nP1\tPedro\n')
        code, _, err = self.sweep('--names', names, '--control', 'Ana Ibarrola Zubiri')
        self.assertEqual(code, 2)
        self.assertIn('line 2', err)

    def test_a_gzipped_dump_is_read(self):
        import gzip
        gz = os.path.join(self.dir, 'tree.json.gz')
        with open(os.path.join(FIX, 'tree.json'), 'rb') as src, gzip.open(gz, 'wb') as dst:
            dst.write(src.read())
        code, out, _ = self.sweep('--dump', gz, '--control', 'José Olmedo Quiroga')
        self.assertEqual(code, 0)
        self.assertIn('EX2\tLucía Valverde Ortega\tname', out)

    def test_no_control_is_bad_input(self):
        self.assertEqual(self.sweep('--names', os.path.join(FIX, 'persons.tsv'))[0], 2)


class XrefTest(unittest.TestCase):
    def xref(self, *argv):
        code, out, err = run('xref', os.path.join(FIX, 'dictionary.txt'), '--dump', os.path.join(FIX, 'tree.json'),
                             *argv)
        return code, (json.loads(out)['candidates'] if code in (0, 1) else out), err

    def found(self, cands):
        return {(c['person'], c['match'], c['page']) for c in cands}

    def test_an_inverted_dictionary_heading_matches_the_full_name(self):
        code, cands, _ = self.xref()
        self.assertEqual(code, 0)
        self.assertIn(('EX1', 'name', 2), self.found(cands))

    def test_candidates_are_rank_input_with_page_and_md5(self):
        _, cands, _ = self.xref()
        c = next(c for c in cands if c['person'] == 'EX1')
        self.assertRegex(c['id'], r'^md5:[0-9a-f]{32}#p2$')
        self.assertFalse(c['principal'])
        sys.path.insert(0, os.path.join(HERE, '..', 'rank'))
        import gwt_rank
        self.assertEqual(len(gwt_rank.normalized_rows({'candidates': cands})), len(cands))

    def test_surname_pair_and_place(self):
        _, cands, _ = self.xref()
        found = self.found(cands)
        self.assertIn(('EX3', 'pair', 3), found)
        self.assertIn(('EX2', 'place', 3), found)  # Valverde and Robledal, the town above her death's church
        self.assertNotIn(('EX2', 'place', 1), found)  # Robledal alone is not her

    def test_a_pair_on_too_many_pages_is_dropped(self):
        _, cands, _ = self.xref('--max-pair-pages', '0')
        self.assertNotIn(('EX3', 'pair', 3), self.found(cands))

    def test_a_common_surname_gives_no_place_match(self):
        _, cands, _ = self.xref('--max-surname-people', '0')
        self.assertFalse([c for c in cands if c['match'] == 'place'])

    def test_a_heading_needs_a_capitalised_surname_block(self):
        self.assertIn('José Olmedo Quiroga', C.flip_headings('OLMEDO QUIROGA, José 12'))
        self.assertEqual(C.flip_headings('Olmedo Quiroga, José'), 'Olmedo Quiroga, José')

    def test_tsv_format(self):
        code, out, _ = run('xref', os.path.join(FIX, 'dictionary.txt'), '--dump', os.path.join(FIX, 'tree.json'),
                           '--format', 'tsv')
        self.assertEqual(code, 0)
        self.assertIn('name\tEX1\tJosé Olmedo Quiroga\t2\t', out)

    def write(self, name, text):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, name)
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        return path

    def tree(self, *people):
        objs = {'people': [], 'events': [], 'places': []}
        for n, (given, surname, town) in enumerate(people, 1):
            p = {'gramps_id': 'T%d' % n, 'primary_name': {'first_name': given, 'surname_list': [{'surname': surname}]},
                 'event_ref_list': [], 'birth_ref_index': -1, 'death_ref_index': -1}
            if town:
                objs['events'].append({'handle': 'e%d' % n, 'place': 'pl%d' % n})
                objs['places'].append({'handle': 'pl%d' % n, 'name': {'value': town}, 'place_type': 'Town'})
                p['event_ref_list'], p['birth_ref_index'] = [{'ref': 'e%d' % n}], 0
            objs['people'].append(p)
        return self.write('tree.json', json.dumps({'objects': objs}))

    def test_particles_never_make_a_pair_or_a_place_match(self):
        tree = self.tree(('Juan', 'García de la Torre', 'Villazul'))
        book = self.write('book.txt', 'Acta de la sesión celebrada en Villazul.\fEl molino de la torre vieja.\f')
        code, out, err = run('xref', book, '--dump', tree, '--format', 'tsv')
        self.assertEqual(code, 1, out)
        self.assertIn('0 matches in 2 pages', err)
        book = self.write('book2.txt', 'Juan García de la Torre, vecino.\fLos García de la Torre de Villazul.')
        code, out, _ = run('xref', book, '--dump', tree, '--format', 'tsv')
        self.assertIn('name\tT1', out)
        self.assertIn('pair\tT1', out)

    def test_a_two_word_name_on_many_pages_is_no_lead(self):
        tree = self.tree(('Juan', 'Smith', ''), ('Mary', 'Smith', ''))
        book = self.write('book.txt', '\f'.join(['Juan Smith signed.'] * 8 + ['Mary Smith signed.']))
        code, out, _ = run('xref', book, '--dump', tree, '--format', 'tsv')
        self.assertNotIn('T1', out)
        self.assertIn('name\tT2', out)

    def test_a_town_and_surname_on_many_pages_is_no_lead(self):
        tree = self.tree(('Rosa', 'Larrinaga Soto', 'Villazul'))
        book = self.write('book.txt', '\f'.join(['Soto y Villazul, hoja %d.' % i for i in range(8)]))
        code, out, _ = run('xref', book, '--dump', tree, '--format', 'tsv')
        self.assertNotIn('place', out)
        code, out, _ = run('xref', book, '--dump', tree, '--format', 'tsv', '--max-place-pages', '8')
        self.assertIn('place\tT1', out)

    def test_a_word_broken_over_a_windows_line_end_is_joined(self):
        tree = self.tree(('Tomasa', 'Ugarte Larrinaga', ''))
        book = self.write('book.txt', 'Tomasa Ugar-\r\nte Larrinaga, maestra.')
        code, out, _ = run('xref', book, '--dump', tree, '--format', 'tsv')
        self.assertIn('name\tT1', out)

    def test_a_pdf_pdftotext_cannot_read_is_bad_input(self):
        with tempfile.NamedTemporaryFile(suffix='.pdf') as f, \
                mock.patch.object(C.subprocess, 'run', return_value=mock.Mock(returncode=1, stdout=b'')), \
                mock.patch.object(C.shutil, 'which', return_value='/bin/pdftotext'):
            code, _, err = run('xref', f.name, '--dump', os.path.join(FIX, 'tree.json'))
        self.assertEqual(code, 2)
        self.assertIn('could not read', err)

    def test_a_scanned_pdf_with_no_text_is_bad_input(self):
        with tempfile.NamedTemporaryFile(suffix='.pdf') as f, \
                mock.patch.object(C.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=b'\f\f')), \
                mock.patch.object(C.shutil, 'which', return_value='/bin/pdftotext'):
            code, _, err = run('xref', f.name, '--dump', os.path.join(FIX, 'tree.json'))
        self.assertEqual(code, 2)
        self.assertIn('no text layer', err)

    def test_name_words_apart_on_a_page_are_no_match(self):
        tree = self.tree(('Rosa', 'Larrinaga Soto', ''))
        book = self.write('book.txt', 'Rosa vino sola. Larrinaga firma. Soto testigo.')
        code, out, _ = run('xref', book, '--dump', tree, '--format', 'tsv')
        self.assertEqual(code, 1, out)

    def test_a_file_that_is_no_dump_is_bad_input(self):
        code, _, err = run('xref', os.path.join(FIX, 'dictionary.txt'), '--dump', self.write('x.json', '{"a": 1}'))
        self.assertEqual(code, 2)
        self.assertIn('not a gwt-check dump', err)

    def test_a_pdf_without_pdftotext_is_bad_input(self):
        with tempfile.NamedTemporaryFile(suffix='.pdf') as f, \
                mock.patch.object(C.shutil, 'which', return_value=None):
            code, _, err = run('xref', f.name, '--dump', os.path.join(FIX, 'tree.json'))
        self.assertEqual(code, 2)
        self.assertIn('pdftotext', err)


class CliTest(unittest.TestCase):
    def test_the_wrapper_runs(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, 'gwt-corpus'), '--help'], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn('sweep', r.stdout)


if __name__ == '__main__':
    unittest.main()
