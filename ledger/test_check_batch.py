"""gwt-ledger check --jsonl: many checks against one read of the ledger. Invented people and sources only."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import gwt_ledger as L

DAY = '2026-06-01'


class CheckBatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'ledger.tsv')
        self.lanes = os.path.join(self.tmp.name, 'lanes')
        self.batch = os.path.join(self.tmp.name, 'checks.jsonl')

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = L.main(['--ledger', self.path, '--ledger-dir', self.lanes, *argv])
            except SystemExit as e:  # argparse usage errors
                code = e.code
        self.last_error = err.getvalue()
        return code, out.getvalue()

    def add(self, person, source, query, result, agent, *extra):
        code, _ = self.ledger('add', '--person', person, '--label', person, '--source', source, '--query', query,
                              '--result', result, '--agent', agent, '--control-ok', 'run-1', '--date', '2026-01-01',
                              *extra)
        self.assertEqual(code, 0, self.last_error)

    def seed(self):
        """Rows of every kind check tells apart; each has its own agent, so a row is known by its agent."""
        self.add('Ana Example', 'fs', 'Ana Example baptism 1850-1860', 'none', 'a1')       # an alias of the source
        self.add('Ana Example', 'familysearch', 'Ana Example marriage 1875', 'blocked', 'a2')
        self.add('Ana Example', 'familysearch', 'Ana Example burial 1910', 'hit', 'a3', '--next-date', '2026-03-01')
        self.add('Ana Example', 'familysearch', 'Ana Example census 1880', 'hit', 'a4', '--next-date', '2026-12-01')
        self.add('Luis Example', 'familysearch', 'Ana Example baptism 1850-1860', 'hit', 'a5')
        self.add('Ana Example', 'pares', 'Ana Example padron 1900', 'hit', 'a7')
        self.add('Ana Example', 'pares', 'Ana Example padron 1900 wrong', 'hit', 'a8')
        code, _ = self.ledger('void', '--person', 'Ana Example', '--source', 'pares', '--query',
                              'Ana Example padron 1900 wrong', '--reason', 'another Ana', '--agent', 'a9')
        self.assertEqual(code, 0, self.last_error)

    def run_batch(self, lines, *extra):
        with open(self.batch, 'w', encoding='utf-8') as f:
            f.write(''.join((l if isinstance(l, str) else json.dumps(l)) + '\n' for l in lines))
        code, out = self.ledger('check', '--date', DAY, '--jsonl', self.batch, *extra)
        return code, [json.loads(l) for l in out.splitlines()]

    def single(self, line):
        argv = ['check', '--date', DAY]
        for name in ('person', 'source', 'query'):
            if line.get(name) is not None:
                argv += ['--' + name, line[name]]
        if line.get('exact'):
            argv.append('--exact')
        if line.get('any_result'):
            argv.append('--any-result')
        if line.get('threshold') is not None:
            argv += ['--threshold', str(line['threshold'])]
        code, out = self.ledger(*argv)
        agents = [l.split('\t')[1 + L.COLUMNS.index('agent')] for l in out.splitlines()]
        return code, agents

    CHECKS = [
        {'person': 'Ana Example', 'source': 'familysearch', 'query': 'Ana Example baptism 1850-1860'},
        {'person': 'ana-example', 'source': 'fs', 'query': 'baptism of Ana Example, 1850 1860'},
        {'person': 'ANA EXAMPLE', 'source': 'familysearch', 'query': 'Ana Example baptism 1850-1860', 'exact': True},
        {'person': 'Ana Example', 'source': 'familysearch', 'query': 'Ana Example marriage 1875'},
        {'person': 'Ana Example', 'source': 'familysearch', 'query': 'Ana Example marriage 1875', 'any_result': True},
        {'person': 'Ana Example', 'source': 'familysearch', 'query': 'Ana Example burial 1910'},
        {'person': 'Ana Example', 'source': 'familysearch', 'query': 'Ana Example census 1880'},
        {'person': 'Ana Example', 'source': 'familysearch'},
        {'person': 'Ana Example', 'query': 'Ana Example baptism 1850-1860'},
        {'query': 'Ana Example baptism 1850-1860'},
        {'source': 'familysearch', 'query': 'Ana Example baptism'},
        {'person': 'Ana Example', 'source': 'pares', 'query': 'Ana Example padron 1900 wrong', 'exact': True},
        {'person': 'Ana Example', 'source': 'pares', 'query': 'Ana Example padron 1900'},
        {'person': 'Ana Example', 'source': 'familysearch', 'query': 'Ana Example baptism', 'threshold': 0.99},
        {'person': 'Nobody Example', 'source': 'familysearch', 'query': 'Ana Example baptism 1850-1860'},
        {'person': 'Ana Example', 'source': 'example-archive', 'query': 'Ana Example baptism 1850-1860'},
    ]

    def test_batch_answers_equal_single_answers(self):
        self.seed()
        code, answers = self.run_batch(self.CHECKS)
        self.assertEqual(code, 0, self.last_error)
        self.assertEqual(len(answers), len(self.CHECKS))
        seen = set()
        for line, answer in zip(self.CHECKS, answers):
            single_code, single_agents = self.single(line)
            self.assertIn(single_code, (0, 1), (line, self.last_error))
            self.assertEqual(answer['done'], single_code == 0, line)
            self.assertEqual([r['agent'] for r in answer['rows']], single_agents, line)
            seen.add((answer['done'], bool(answer['rows'])))
        # the inputs reach every answer: done and not done, with and without rows
        self.assertEqual(seen, {(True, True), (False, True), (False, False)})

    def test_each_answer_echoes_its_line_in_input_order(self):
        self.seed()
        lines = [self.CHECKS[3], self.CHECKS[0]]
        code, answers = self.run_batch(lines)
        self.assertEqual(code, 0)  # the batch ran, though one search is not done
        self.assertEqual([{k: a[k] for k in line} for a, line in zip(answers, lines)], lines)
        self.assertEqual([a['done'] for a in answers], [False, True])
        row = answers[1]['rows'][0]
        self.assertEqual(set(row), {'id', 'date', 'result', 'agent', 'next_date'})
        self.assertEqual((row['date'], row['result'], row['agent']), ('2026-01-01', 'none', 'a1'))
        self.assertRegex(row['id'], r'^[0-9a-f]{12}$')

    def test_a_line_takes_the_command_line_options_it_leaves_out(self):
        self.seed()
        line = {'person': 'Ana Example', 'source': 'familysearch', 'query': 'Ana Example marriage 1875'}
        self.assertEqual(self.run_batch([line], '--any-result')[1][0]['done'], True)
        self.assertEqual(self.run_batch([dict(line, any_result=False)], '--any-result')[1][0]['done'], False)
        self.assertEqual(self.run_batch([dict(line, any_result=True)])[1][0]['done'], True)
        fuzzy = {'person': 'Ana Example', 'source': 'familysearch', 'query': 'baptism of Ana Example, 1850 1860'}
        self.assertEqual(self.run_batch([fuzzy])[1][0]['done'], True)
        self.assertEqual(self.run_batch([dict(fuzzy, exact=True)])[1][0]['done'], False)
        self.assertEqual(self.run_batch([fuzzy], '--exact')[1][0]['done'], False)

    def test_the_ledger_is_read_once_for_the_whole_batch(self):
        self.seed()
        with mock.patch.object(L, 'read_rows', wraps=L.read_rows) as read:
            code, answers = self.run_batch(self.CHECKS)
        self.assertEqual((code, len(answers)), (0, len(self.CHECKS)))
        self.assertEqual(read.call_count, 1 + len(os.listdir(self.lanes)))

    def test_stdin_and_blank_lines(self):
        self.seed()
        stdin = io.StringIO('\n' + json.dumps(self.CHECKS[0]) + '\n\n')
        with mock.patch('sys.stdin', stdin):
            code, out = self.ledger('check', '--date', DAY, '--jsonl', '-')
        self.assertEqual(code, 0, self.last_error)
        self.assertEqual([json.loads(l)['done'] for l in out.splitlines()], [True])
        self.assertFalse(stdin.closed, 'stdin belongs to the caller: a second read in the same process must still work')

    def test_a_bad_line_stops_the_batch_naming_it_and_prints_no_answer(self):
        self.seed()
        good = self.CHECKS[0]
        for bad, why in [
            ('{"person": "Ana Example", ', 'line 2: '),
            ('["Ana Example"]', 'line 2: needs a JSON object'),
            ({'person': 'Ana Example', 'query': 'x', 'exactt': True}, 'line 2: unknown field(s) exactt'),
            ({'person': 'Ana Example', 'query': 'x', 'exact': 'yes'}, 'line 2: exact must be true or false'),
            ({'person': 'Ana Example', 'query': 'x', 'threshold': True}, 'line 2: threshold must be a number'),
            ({'person': 42, 'query': 'x'}, 'line 2: person must be a string'),
            ({'person': 'Ana Example', 'query': '!!'}, "line 2: --query '!!': nothing left to match"),
            ({'person': '', 'query': 'x'}, "line 2: --person '': nothing left to match"),
            ({'person': 'Ana Example', 'query': 'x', 'exact': True}, 'line 2: check --exact needs a non-empty'),
            ({'person': 'Ana Example', 'source': 'familysearch', 'exact': True}, 'line 2: check --exact needs --query'),
            ({'source': 'familysearch'}, 'line 2: check needs --query'),
            ({'person': 'Ana Example', 'source': 'familysea', 'query': 'x'}, 'line 2: --source '),
        ]:
            with self.subTest(bad=bad):
                code, answers = self.run_batch([good, bad, good])
                self.assertEqual((code, answers), (2, []))
                self.assertIn('gwt-ledger: ' + why, self.last_error)

    def test_jsonl_takes_no_search_from_the_command_line(self):
        self.seed()
        for flag in ('--person', '--source', '--query'):
            with self.subTest(flag=flag):
                code, answers = self.run_batch([self.CHECKS[0]], flag, 'familysearch')
                self.assertEqual((code, answers), (2, []))
                self.assertIn('check --jsonl takes person, source and query from its lines', self.last_error)

    def test_a_missing_file_is_a_usage_error(self):
        code, out = self.ledger('check', '--jsonl', os.path.join(self.tmp.name, 'none.jsonl'))
        self.assertEqual((code, out), (2, ''))
        self.assertIn('cannot read', self.last_error)


if __name__ == '__main__':
    unittest.main()
