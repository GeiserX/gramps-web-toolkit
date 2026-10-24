import datetime, io, os, subprocess, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_queue as Q

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gwt-queue')


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = Q.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'cola.tsv')

    def tearDown(self):
        self.dir.cleanup()

    def add(self, *extra, person='Ana Peiro Toledo', kind='sweep', source='familysearch',
            query='ana peiro 1850', priority=0):
        return run('--queue', self.path, 'add', '--person', person, '--label', person, '--kind', kind,
                   '--source', source, '--query', query, '--priority', str(priority), *extra)

    def rows(self):
        with open(self.path) as f:
            lines = f.read().splitlines()
        return [dict(zip(Q.COLUMNS, l.split('\t'))) for l in lines[1:]]

    def tok(self, task_id):
        return next(r['token'] for r in self.rows() if r['id'] == task_id)

    def test_add_writes_header_and_one_row(self):
        code, out, _ = self.add()
        self.assertEqual(code, 0)
        r = self.rows()[0]
        self.assertEqual(r['id'], 'q1')
        self.assertEqual(r['person_key'], 'ana-peiro-toledo')
        self.assertEqual(r['status'], 'open')
        self.assertEqual(r['tries'], '0')

    def test_ids_increase(self):
        self.add(); self.add(person='Jose Ruiz', query='jose ruiz 1880')
        self.assertEqual([r['id'] for r in self.rows()], ['q1', 'q2'])

    def test_dedupe_skips_open_duplicate(self):
        self.add()
        code, out, _ = self.add('--dedupe')
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows()), 1)
        self.assertTrue(out.startswith('q1\t'))

    def test_dedupe_allows_same_search_once_done(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'done', '--id', 'q1', '--token', self.tok('q1'), '--result', 'none')
        self.add('--dedupe')
        self.assertEqual(len(self.rows()), 2)

    def test_claim_takes_highest_priority_first(self):
        self.add(priority=10)
        self.add(person='Jose Ruiz', query='jose ruiz 1880', priority=100)
        code, out, _ = run('--queue', self.path, 'claim', '--lane', 'lane-2')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('q2\t'))
        r = self.rows()[1]
        self.assertEqual(r['status'], 'claimed')
        self.assertEqual(r['lane'], 'lane-2')
        self.assertEqual(r['tries'], '1')

    def test_claim_empty_exits_1(self):
        code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
        self.assertEqual(code, 1)

    def test_claim_skips_not_yet_due(self):
        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
                  ).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.add('--next-at', future)
        code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
        self.assertEqual(code, 1)

    def test_claim_filters_by_kind_and_source(self):
        self.add(kind='search', source='gaceta', query='ana gaceta')
        code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l1', '--kind', 'sweep')
        self.assertEqual(code, 1)
        code, out, _ = run('--queue', self.path, 'claim', '--lane', 'l1', '--source', 'Gaceta')
        self.assertEqual(code, 0)

    def test_two_lanes_never_get_the_same_task(self):
        self.add(); self.add(person='Jose Ruiz', query='jose ruiz 1880')
        code1, out1, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
        code2, out2, _ = run('--queue', self.path, 'claim', '--lane', 'l2')
        self.assertEqual((code1, code2), (0, 0))
        self.assertNotEqual(out1.split('\t')[0], out2.split('\t')[0])

    def test_done_records_result_and_evidence(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        code, _, _ = run('--queue', self.path, 'done', '--id', 'q1', '--token', self.tok('q1'), '--result', 'hit',
                         '--evidence', 'hits/ana.txt')
        self.assertEqual(code, 0)
        r = self.rows()[0]
        self.assertEqual(r['status'], 'done')
        self.assertEqual(r['result'], 'hit')
        self.assertEqual(r['evidence'], 'hits/ana.txt')
        self.assertTrue(r['done_at'])

    def test_done_twice_is_an_error(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'done', '--id', 'q1', '--token', self.tok('q1'), '--result', 'none')
        code, _, err = run('--queue', self.path, 'done', '--id', 'q1', '--token', self.tok('q1'), '--result', 'none')
        self.assertEqual(code, 2)

    def test_fail_reopens_with_backoff_then_parks(self):
        self.add()
        for i in range(1, 6):
            run('--queue', self.path, 'claim', '--lane', 'l1')
            code, out, _ = run('--queue', self.path, 'fail', '--id', 'q1', '--token', self.tok('q1'), '--backoff-minutes', '0')
            self.assertEqual(code, 0)
        r = self.rows()[0]
        self.assertEqual(r['status'], 'failed')  # tries reached the default max of 5
        code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l2')
        self.assertEqual(code, 1)  # a failed task is never claimed again

    def test_fail_backoff_grows_with_tries(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'fail', '--id', 'q1', '--token', self.tok('q1'), '--backoff-minutes', '60')
        r = self.rows()[0]
        self.assertEqual(r['status'], 'open')
        gap = (Q.parse_iso(r['next_at']) - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        self.assertGreater(gap, 3500)  # ~60 minutes * 1 try, give or take the test's own seconds

    def test_release_returns_task_without_penalty(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        code, _, _ = run('--queue', self.path, 'release', '--id', 'q1', '--token', self.tok('q1'))
        self.assertEqual(code, 0)
        r = self.rows()[0]
        self.assertEqual(r['status'], 'open')
        self.assertEqual(r['next_at'], '')

    def test_stale_claim_reopens(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'dead-lane')
        # age the claim by hand, then another lane picks the task up
        rows = self.rows()
        rows[0]['claimed_at'] = (datetime.datetime.now(datetime.timezone.utc)
                                 - datetime.timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with open(self.path, 'w') as f:
            f.write('\t'.join(Q.COLUMNS) + '\n')
            for r in rows:
                f.write('\t'.join(r[c] for c in Q.COLUMNS) + '\n')
        code, out, _ = run('--queue', self.path, '--stale-minutes', '20', 'claim', '--lane', 'l2')
        self.assertEqual(code, 0)
        self.assertEqual(self.rows()[0]['lane'], 'l2')

    def test_report_lists_due_waiting_failed(self):
        self.add(priority=100)
        self.add('--next-at', (datetime.datetime.now(datetime.timezone.utc)
                               + datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 person='Jose Ruiz', query='jose ruiz 1880')
        code, out, _ = run('--queue', self.path, 'report')
        self.assertEqual(code, 0)
        self.assertIn('due\t1\t', out)
        self.assertIn('waiting\t1\t', out)
        self.assertIn('q1\t', out)

    def test_list_filters(self):
        self.add()
        self.add(person='Jose Ruiz', query='jose ruiz 1880')
        run('--queue', self.path, 'claim', '--lane', 'l1')
        code, out, _ = run('--queue', self.path, 'list', '--status', 'open')
        self.assertEqual(len([l for l in out.splitlines() if l]), 1)
        code, out, _ = run('--queue', self.path, 'list', '--person', 'josé ruiz')
        self.assertTrue(out.startswith('q2\t'))

    def test_stats_counts_open_work(self):
        self.add(); self.add(person='Jose Ruiz', query='jose ruiz 1880')
        run('--queue', self.path, 'claim', '--lane', 'l1')
        code, out, _ = run('--queue', self.path, 'stats')
        self.assertIn('status\tclaimed\t1', out)
        self.assertIn('status\topen\t1', out)

    def test_cli_entrypoint(self):
        p = subprocess.run([CLI, '--queue', self.path, 'add', '--person', 'Ana Peiro',
                            '--kind', 'search', '--source', 'fs', '--query', 'ana'],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        p = subprocess.run([CLI, '--queue', self.path, 'claim', '--lane', 'l1'],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.startswith('q1\t'))

    # --- contract fixes found in review ---

    def age_claim(self, task_id, minutes=30):
        rows = self.rows()
        for r in rows:
            if r['id'] == task_id:
                r['claimed_at'] = (datetime.datetime.now(datetime.timezone.utc)
                                   - datetime.timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with open(self.path, 'w') as f:
            f.write('\t'.join(Q.COLUMNS) + '\n')
            for r in rows:
                f.write('\t'.join(r[c] for c in Q.COLUMNS) + '\n')

    def test_old_worker_cannot_finish_a_task_reassigned_after_its_claim_went_stale(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'old')
        old_token = self.tok('q1')
        self.age_claim('q1')
        code, _, _ = run('--queue', self.path, '--stale-minutes', '20', 'claim', '--lane', 'new')
        self.assertEqual(code, 0)
        new_token = self.tok('q1')
        self.assertNotEqual(old_token, new_token)
        for cmd in (('done', '--result', 'hit'), ('fail',), ('release',)):
            code, _, err = run('--queue', self.path, cmd[0], '--id', 'q1', '--token', old_token, *cmd[1:])
            self.assertEqual(code, 2, cmd)
            self.assertIn('another claim', err)
        r = self.rows()[0]
        self.assertEqual((r['status'], r['lane'], r['token']), ('claimed', 'new', new_token))
        code, _, _ = run('--queue', self.path, 'done', '--id', 'q1', '--token', new_token, '--result', 'none')
        self.assertEqual(code, 0)

    def test_wrong_or_missing_token_is_refused(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        code, _, err = run('--queue', self.path, 'done', '--id', 'q1', '--token', 'nope', '--result', 'none')
        self.assertEqual(code, 2)
        self.assertEqual(self.rows()[0]['status'], 'claimed')
        with self.assertRaises(SystemExit):
            run('--queue', self.path, 'done', '--id', 'q1', '--result', 'none')

    def test_fail_on_a_done_task_does_not_reopen_it(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        token = self.tok('q1')
        run('--queue', self.path, 'done', '--id', 'q1', '--token', token, '--result', 'hit')
        before = self.rows()[0]
        code, _, err = run('--queue', self.path, 'fail', '--id', 'q1', '--token', token)
        self.assertEqual(code, 2)
        self.assertIn('not claimed', err)
        self.assertEqual(self.rows()[0], before)  # still done, result and done_at intact

    def test_done_needs_a_claimed_task(self):
        self.add()  # open, never claimed
        code, _, err = run('--queue', self.path, 'done', '--id', 'q1', '--token', 'x', '--result', 'none')
        self.assertEqual(code, 2)
        self.assertIn('not claimed', err)
        self.assertEqual(self.rows()[0]['status'], 'open')
        # a parked task cannot be completed either
        for _ in range(5):
            run('--queue', self.path, 'claim', '--lane', 'l1')
            run('--queue', self.path, 'fail', '--id', 'q1', '--token', self.tok('q1'), '--backoff-minutes', '0')
        self.assertEqual(self.rows()[0]['status'], 'failed')
        code, _, _ = run('--queue', self.path, 'done', '--id', 'q1', '--token', 'x', '--result', 'none')
        self.assertEqual(code, 2)

    def test_write_is_atomic_and_leaves_no_temp_file(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        left = sorted(os.listdir(self.dir.name))
        self.assertEqual(left, ['cola.tsv', 'cola.tsv.lock'])

    def test_crash_during_write_keeps_the_old_queue(self):
        self.add()
        with open(self.path) as f:
            before = f.read()
        real = os.replace

        def boom(src, dst):
            raise OSError('simulated crash before the rename')
        Q.os.replace = boom
        try:
            with self.assertRaises(OSError):
                self.add(person='Jose Ruiz', query='jose ruiz 1880')
        finally:
            Q.os.replace = real
        with open(self.path) as f:
            self.assertEqual(f.read(), before)  # old file whole, not truncated
        self.assertEqual(sorted(os.listdir(self.dir.name)), ['cola.tsv', 'cola.tsv.lock'])  # no temp file left
        code, _, _ = self.add(person='Jose Ruiz', query='jose ruiz 1880')  # and the queue still works
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows()), 2)

    def test_a_queue_written_before_tokens_still_reads(self):
        old = [c for c in Q.COLUMNS if c != 'token']
        with open(self.path, 'w') as f:
            f.write('\t'.join(old) + '\n')
            f.write('\t'.join(['q1', 'ana', 'Ana', 'sweep', '', 'ana', '5', 'open'] + [''] * 7 + ['0', '', '']) + '\n')
        code, out, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
        self.assertEqual(code, 0)
        self.assertTrue(self.tok('q1'))

    def test_parallel_processes_never_share_a_task_across_atomic_replaces(self):
        # every write swaps the file's inode; the lock lives on a separate file, so claims still exclude each other
        for i in range(12):
            self.add(person='P%d' % i, query='q%d' % i)
        procs = [subprocess.Popen([CLI, '--queue', self.path, 'claim', '--lane', 'l%d' % n],
                                  stdout=subprocess.PIPE, text=True) for n in range(12)]
        ids = []
        for p in procs:
            out, _ = p.communicate()
            self.assertEqual(p.returncode, 0)
            ids.append(out.split('\t')[0])
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual({r['status'] for r in self.rows()}, {'claimed'})


if __name__ == '__main__':
    unittest.main()
