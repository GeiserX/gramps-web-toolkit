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

    def test_a_pdf_without_pdftotext_is_counted_not_silent(self):
        with open(os.path.join(self.dir, 'books', 'book.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 invented')
        with mock.patch.object(C.shutil, 'which', return_value=None):
            code, out, _ = run('update', '--db', self.db, *self.roots)
        self.assertIn('1 PDFs not read: pdftotext', out)


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
        self.assertIn(('k-olmedo', '"jose olmedo quiroga"', 'hit'), got)
        self.assertIn(('k-merino', '"tomas merino santos"', 'hit'), got)
        self.assertIn(('k-absent', '"elena zubiri calvo"', 'none'), got)
        self.assertIn(('k-absent', '"zubiri calvo"', 'none'), got)
        self.assertTrue(all(r[2] == 'corpus' and r[7] == 'lane-a' for r in rows))
        self.assertIn('Tomás Merino (the miller)', out)

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
            if expr == '"ana ibarrola zubiri"' and len(calls) > 1:
                return []  # the index went away during the run
            return real(db, expr, source, limit)

        with mock.patch.object(C, 'run_query', flaky):
            code, out, _ = self.sweep('--names', os.path.join(FIX, 'persons.tsv'), '--control',
                                      'Ana Ibarrola Zubiri', '--ledger', ledger)
        self.assertEqual(code, 3)
        self.assertIn('after the sweep', out)
        self.assertFalse(os.path.exists(ledger))

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
