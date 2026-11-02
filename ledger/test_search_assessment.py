"""Invented identifiers only. The assessment does no network or ledger I/O."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock
import gwt_ledger as L


class AssessmentTest(unittest.TestCase):
    def assess(self, **changes):
        data = dict(method='images', outcome='none', access='open', coverage='full',
                    scope='Invented parish, births 1800-1801, images 3-9', ref='invented-evidence.txt')
        data.update(changes)
        return L.assess_search(**data)

    def test_access_failures_are_not_negatives_even_for_claimed_hits(self):
        for access in ('restricted', 'captcha', 'load-error', 'maintenance', 'unknown'):
            for outcome in ('none', 'hit'):
                with self.subTest(access=access, outcome=outcome):
                    r = self.assess(access=access, outcome=outcome)
                    self.assertEqual(r['result'], 'blocked')
                    self.assertIn(access, r['reasons'][0])
                    self.assertIn('not evidence of absence', r['claim'])

    def test_incomplete_coverage_is_partial_not_absence_or_confirmed_hit(self):
        for coverage in ('partial', 'outside', 'unknown'):
            for outcome in ('none', 'hit'):
                r = self.assess(coverage=coverage, outcome=outcome)
                self.assertEqual(r['result'], 'partial')
                self.assertIn('no absence claim', r['claim'])

    def test_index_and_ocr_need_both_checks(self):
        for method in ('ocr', 'index'):
            for control, filters in ((False, False), (True, False), (False, True)):
                for outcome in ('none', 'hit'):
                    r = self.assess(method=method, outcome=outcome,
                                    control_verified=control, filters_verified=filters)
                    self.assertEqual(r['result'], 'partial')
            r = self.assess(method=method, control_verified=True, filters_verified=True)
            self.assertEqual(r['result'], 'none')
            self.assertIn('query', r['claim'])
            self.assertNotIn('does not exist', r['claim'])
            self.assertEqual(r['reasons'], [])

    def test_claims_are_method_scoped(self):
        self.assertIn('images actually reviewed', self.assess()['claim'])
        self.assertIn('inspected catalogue scope', self.assess(method='catalog')['claim'])

    def test_hit_does_not_prove_kinship(self):
        for method in ('images', 'index', 'ocr', 'catalog'):
            r = self.assess(method=method, outcome='hit', control_verified=True, filters_verified=True)
            self.assertEqual(r['result'], 'hit')
            self.assertFalse(r['kinship_proven'])
            self.assertIn('remain unproved', r['claim'])

    def test_scope_and_evidence_are_required_and_preserved(self):
        for field in ('scope', 'ref'):
            for value in ('', '   '):
                with self.assertRaises(ValueError):
                    self.assess(**{field: value})
        r = self.assess(scope='Exact CASE; 1801-1803; images 2-4', ref='https://example.invalid/evidence')
        self.assertEqual(r['scope'], 'Exact CASE; 1801-1803; images 2-4')
        self.assertEqual(r['ref'], 'https://example.invalid/evidence')

    def test_cli_outputs_json_without_reading_or_changing_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'not-a-ledger')
            with open(path, 'w') as f:
                f.write('KEEP')
            # Neither explicit nor environment paths may trigger discovery or I/O.
            for paths in ([], ['--ledger', path, '--ledger-dir', d]):
                for method in ('images', 'ocr'):
                    out = io.StringIO()
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(mock.patch.dict(os.environ, {
                            'GWT_LEDGER': path, 'GWT_LEDGER_DIR': d}))
                        for target in ('read_rows', 'add_row', 'ledger_paths'):
                            stack.enter_context(mock.patch.object(
                                L, target, side_effect=AssertionError('unexpected ledger I/O')))
                        stack.enter_context(mock.patch('builtins.open', side_effect=AssertionError('unexpected open')))
                        stack.enter_context(mock.patch.object(L.glob, 'glob', side_effect=AssertionError('unexpected glob')))
                        stack.enter_context(contextlib.redirect_stdout(out))
                        rc = L.main(paths + ['assess', '--method', method, '--outcome', 'none',
                                    '--access', 'open', '--coverage', 'full', '--scope', 'invented images 1-4',
                                    '--ref', path, '--control-verified', '--filters-verified'])
                    self.assertEqual(rc, 0)
                    self.assertEqual(json.loads(out.getvalue()), self.assess(
                        method=method, scope='invented images 1-4', ref=path,
                        control_verified=True, filters_verified=True))
            with open(path) as f:
                self.assertEqual(f.read(), 'KEEP')

    def test_cli_rejects_bad_arguments(self):
        base = ['assess', '--method', 'images', '--outcome', 'none', '--access', 'open',
                '--coverage', 'full', '--scope', 'invented', '--ref', 'invented.txt']
        for flag, value in (('--method', 'magic'), ('--outcome', 'confirmed'), ('--access', 'probably'),
                            ('--coverage', 'complete-ish'), ('--scope', ' '), ('--ref', ' ')):
            argv = list(base)
            argv[argv.index(flag) + 1] = value
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                L.main(argv)
            self.assertEqual(exc.exception.code, 2)
        for flag in ('--method', '--outcome', '--access', '--coverage', '--scope', '--ref'):
            argv = list(base)
            index = argv.index(flag)
            del argv[index:index + 2]
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                L.main(argv)
            self.assertEqual(exc.exception.code, 2)
