"""Invented books and objectives; no family-tree data."""
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

    def run_scan(self, action, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = L.main(['--ledger', self.path, 'scan', action, *extra])
        self.last_error = err.getvalue()
        return code, out.getvalue()

    def common(self, start=1, end=10, book='archive:book-1', objective='target-baptism', review='original'):
        return ['--source', 'familysearch', '--book', book, '--objective', objective,
                '--start', str(start), '--end', str(end), '--review', review]

    def add(self, start=1, end=10, coverage='complete', legibility='complete', result='partial',
            book='archive:book-1', objective='target-baptism', review='original', control='run-ok'):
        return self.run_scan('add', *self.common(start, end, book, objective, review),
                             '--coverage', coverage, '--legibility', legibility, '--result', result,
                             '--control-ok', control, '--agent', 'reader-1', '--source-url',
                             'https://example.org/book-1', '--evidence', 'private/notes.md')

    def check(self, *args):
        code, out = self.run_scan('check', *self.common(*args))
        return code, json.loads(out)

    def test_empty_and_union_gaps(self):
        self.assertEqual(self.check()[1]['gaps'], [[1, 10]])
        self.add(1, 4); self.add(6, 10)
        self.assertEqual(self.check()[1]['gaps'], [[5, 5]])
        self.add(5, 5)
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.check(1, 12)[1]['gaps'], [[11, 12]])

    def test_book_and_objective_and_review_are_exact(self):
        self.add()
        for args in [(1, 10, 'archive book 1'), (1, 10, 'archive:Book-1'),
                     (1, 10, 'archive:book-1', 'other'),
                     (1, 10, 'archive:book-1', 'target-baptism', 'second-reading')]:
            self.assertEqual(self.check(*args)[0], 1)

    def test_partial_legibility_is_traversed_not_fully_read(self):
        self.add(legibility='partial')
        code, data = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(data['traversed'])
        self.assertFalse(data['covered'])
        self.assertEqual(len(data['overlaps']), 1)

    def test_incomplete_coverage_never_fills_gap(self):
        self.add(coverage='partial')
        self.assertEqual(self.check()[1]['traversal_gaps'], [[1, 10]])

    def test_blocked_and_unproven_never_count(self):
        for result, control in [('blocked', 'run-ok'), ('none', '')]:
            self.add(result=result, control=control)
        self.assertEqual(self.check()[0], 1)
        self.assertFalse(self.check()[1]['traversed'])

    def test_proven_none_counts_but_not_absence_elsewhere(self):
        self.add(result='none')
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.check(11, 12)[0], 1)

    def test_idempotent_and_void_using_existing_ledger(self):
        self.add(); self.add()
        self.assertEqual(len(L.read_rows(self.path)), 1)
        row = L.read_rows(self.path)[0]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(L.main(['--ledger', self.path, 'void', '--person', 'scan-coverage',
                                    '--source', row['source'], '--query', row['query'],
                                    '--reason', 'wrong book']), 0)
        self.assertEqual(self.check()[0], 1)
        self.add()
        self.assertEqual(self.check()[0], 0)

    def test_invalid_ranges_write_nothing(self):
        for start, end in [(0, 10), (11, 10)]:
            self.assertEqual(self.add(start, end)[0], 2)
        self.assertFalse(os.path.exists(self.path))

    def test_existing_free_text_not_interpreted_as_scan(self):
        L.add_row(self.path, dict(source='familysearch', query='book 1 views 1 10', result='hit'))
        self.assertEqual(self.check()[1]['overlaps'], [])

    def test_list_and_source_alias(self):
        self.add()
        code, out = self.run_scan('list', '--source', 'fs')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['scan']['book'], 'archive:book-1')
        code, out = self.run_scan('list', '--source', 'boe')
        self.assertEqual(out, '')

    def test_malformed_scan_metadata_is_clear_error_even_outside_filter(self):
        changes = [lambda m: m.pop('coverage'), lambda m: m.pop('legibility'),
                   lambda m: m.update(book=42), lambda m: m.update(start=True),
                   lambda m: m.update(end=1.5), lambda m: m.update(unit='unknown'),
                   lambda m: m.update(review='unknown'), lambda m: m.update(coverage=[]),
                   lambda m: m.update(legibility='unknown'), lambda m: m.clear()]
        for change in changes:
            if os.path.exists(self.path):
                os.unlink(self.path)
            self.add()
            row = L.read_rows(self.path)[0]
            metadata = json.loads(row['evidence'])
            change(metadata)
            row['evidence'] = json.dumps(metadata)
            os.unlink(self.path)
            L.add_row(self.path, row)
            for action, extra in [('check', self.common()), ('list', []),
                                  ('list', ['--source', 'boe'])]:
                err = io.StringIO()
                with redirect_stderr(err):
                    code, output = self.run_scan(action, *extra)
                self.assertEqual(code, 2)
                self.assertIn('invalid scan row', self.last_error)
                self.assertNotIn('Traceback', self.last_error)

    def test_non_object_metadata_is_clear_error(self):
        self.add()
        row = L.read_rows(self.path)[0]
        for value in ([], None, 3, 'book'):
            os.unlink(self.path)
            L.add_row(self.path, dict(row, evidence=json.dumps(value)))
            self.assertEqual(self.run_scan('list')[0], 2)


if __name__ == '__main__':
    unittest.main()
