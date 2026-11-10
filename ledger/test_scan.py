"""gwt-ledger scan: image ranges of a book. Invented books and people; no family-tree data."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
import gwt_ledger as L


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'ledger.tsv')

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = L.main(['--ledger', self.path, *argv])
        self.last_error = err.getvalue()
        return code, out.getvalue()

    def add(self, start=1, end=10, *extra, book='book-42', result='hit', source='familysearch'):
        return self.ledger('scan', 'add', '--source', source, '--book', book, '--start', str(start), '--end', str(end),
                        '--result', result, '--evidence', 'notes/book-42.md', '--agent', 'reader-1', *extra)[0]

    def check(self, start=1, end=10, *extra, book='book-42', source='familysearch'):
        code, out = self.ledger('scan', 'check', '--source', source, '--book', book, '--start', str(start),
                             '--end', str(end), *extra)
        return code, json.loads(out) if out else None

    def gaps(self, *args, **kw):
        return self.check(*args, **kw)[1]['gaps']

    def test_ranges_union_and_leave_gaps(self):
        self.assertEqual(self.check(), (1, {'gaps': [[1, 10]], 'rows': []}))
        self.add(1, 4); self.add(6, 10)
        self.assertEqual(self.gaps(), [[5, 5]])
        self.assertEqual(self.check(5, 5)[1]['rows'], [])  # only the rows that overlap the range asked about
        self.add(5, 5)
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.gaps(1, 12), [[11, 12]])

    def test_a_range_inside_a_longer_one_reopens_nothing(self):
        self.add(1, 20); self.add(5, 10)
        self.assertEqual(self.check(1, 20)[0], 0)

    def test_rows_are_ordinary_readable_rows(self):
        self.add(1, 10, book='Book 42')
        row = L.read_rows(self.path)[0]
        self.assertEqual((row['person_key'], row['person_label'], row['query'], row['evidence']),
                         ('', 'Book 42', 'book 42 img 1 10', 'notes/book-42.md'))
        code, out = self.ledger('check', '--source', 'fs', '--exact', '--query', 'book 42 img 1 10')
        self.assertEqual(code, 0)

    def test_hand_written_rows_read_like_check_reads_them(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\t'.join(L.COLUMNS) + '\n' + ''.join('\t'.join(('', 'book-42', 'familysearch', q, '2026-10-01',
                                                                       result, '', 'reader-1', next_date, '')) + '\n'
                                                            for q, result, next_date in (
                ('book 42 img 1 5', 'found', ''), ('book 42 img 6 10', 'hit', '1901 to 1905 still to read'),
                ('book 42 img 11 12', 'maybe', ''))))
        self.assertEqual(self.gaps(1, 12), [[11, 12]])  # found is a hit; a next_date that is no date is never due
        self.assertIn('count as not searched', self.last_error)
        self.assertIn('never due', self.last_error)

    def test_another_book_or_source_never_counts(self):
        self.add(1, 10, book='book-42')
        self.assertEqual(self.check(book='book-420')[0], 1)
        self.assertEqual(self.check(book='book-42', source='boe')[0], 1)
        self.assertEqual(self.check(book='Book 42')[0], 0)  # the book compares normalized, like every query

    def test_a_row_logged_by_hand_in_the_same_shape_counts(self):
        L.add_row(self.path, dict(person_key='', source='fs', query='book 42 img 1 10', date='2026-11-10',
                                  result='hit'))
        self.assertEqual(self.check()[0], 0)

    def test_no_row_can_make_scan_check_fail(self):
        for query in ('gwt scan v1 notes', 'book 42 img 9 3', 'book 42 img 0 4', 'book 42 views 1 10'):
            self.assertEqual(self.ledger('add', '--person', 'x', '--source', 'familysearch', '--query', query,
                                      '--result', 'hit')[0], 0)
        self.add(5, 10)
        self.assertEqual(self.check(), (1, {'gaps': [[1, 4]], 'rows': self.check()[1]['rows']}))
        self.assertEqual([(r['start'], r['end']) for r in self.check()[1]['rows']], [(5, 10)])

    def test_a_read_with_no_person_counts_for_everyone(self):
        self.add(1, 10)
        self.assertEqual(self.check(1, 10, '--person', 'Ana Example')[0], 0)

    def test_a_read_for_one_person_counts_for_that_person_only(self):
        self.add(1, 10, '--person', 'Ana Example')
        self.assertEqual(self.check(1, 10, '--person', 'ana-example')[0], 0)
        self.assertEqual(self.check(1, 10, '--person', 'Luis Example')[0], 1)
        self.assertEqual(self.check(1, 10)[0], 0)  # no --person: every row of the book

    def test_skipped_images_stay_gaps(self):
        self.add(1, 30, '--skip', '12,20-21')
        self.assertEqual(self.gaps(1, 30), [[12, 12], [20, 21]])
        self.assertEqual(len(L.read_rows(self.path)), 3)

    def test_a_bad_skip_or_range_writes_nothing(self):
        for start, end, extra in [(1, 10, ['--skip', '11']), (1, 3, ['--skip', '1-3']), (0, 10, []),
                                  (11, 10, [])]:
            self.assertEqual(self.add(start, end, *extra), 2)
        self.assertEqual(self.add(1, 2, book='!!'), 2)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            L.main(['--ledger', self.path, 'scan', 'add', '--source', 'fs', '--book', 'b', '--start', '1',
                    '--end', '2', '--skip', 'x', '--result', 'hit'])
        self.assertFalse(os.path.exists(self.path))

    def test_blocked_and_unproven_fill_no_gap(self):
        self.add(1, 10, result='blocked')
        self.add(1, 10, result='none')
        self.assertIn('stored as unproven', self.last_error)
        self.assertEqual(self.check()[0], 1)
        self.add(1, 10, '--control-ok', 'run-7', result='none')
        self.assertEqual(self.check()[0], 0)

    def test_a_row_due_again_fills_no_gap(self):
        self.assertEqual(self.ledger('add', '--person', 'x', '--source', 'fs', '--query', 'book 42 img 1 10',
                                     '--result', 'hit', '--next-date', '2026-06-01')[0], 0)
        self.assertEqual(self.check(1, 10, '--date', '2026-05-31')[0], 0)
        self.assertEqual(self.check(1, 10, '--date', '2026-06-01')[0], 1)

    def test_a_control_run_that_proves_nothing_writes_nothing(self):
        self.assertEqual(self.add(1, 10, '--control-ok', 'no', result='none'), 2)
        self.assertIn('not a control run', self.last_error)
        self.assertFalse(os.path.exists(self.path))

    def test_only_void_takes_an_empty_person(self):
        for argv in (['add', '--person', '', '--source', 'fs', '--query', 'q', '--result', 'hit'],
                     ['check', '--person', '', '--source', 'fs'], ['list', '--person', ''],
                     ['scan', 'add', '--person', '', '--source', 'fs', '--book', 'b', '--start', '1', '--end', '2',
                      '--result', 'hit']):
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                L.main(['--ledger', self.path, *argv])
        self.assertFalse(os.path.exists(self.path))

    def test_twice_logs_once(self):
        self.add(1, 30, '--skip', '12'); self.add(1, 30, '--skip', '12')
        self.assertEqual(len(L.read_rows(self.path)), 2)

    def test_void_with_no_person_retracts_only_the_rows_with_no_person(self):
        self.add(1, 10)
        self.add(1, 10, '--person', 'Ana Example')
        self.assertEqual(self.ledger('void', '--person', '', '--source', 'fs', '--query', 'book 42 img 1 10',
                                  '--reason', 'wrong book', '--agent', 'reader-1')[0], 0)
        self.assertEqual(self.check(1, 10, '--person', 'Luis Example')[0], 1)
        self.assertEqual(self.check(1, 10, '--person', 'Ana Example')[0], 0)


if __name__ == '__main__':
    unittest.main()
