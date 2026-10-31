import datetime, io, json, os, re, subprocess, sys, tempfile, unittest
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
        if not os.path.exists(self.path):
            return []
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

    def test_list_result_shows_only_rows_closed_with_that_result(self):
        self.add(); self.add(person='Jose Ruiz', query='jose ruiz 1880')
        for tid, result in (('q1', 'blocked'), ('q2', 'none')):
            run('--queue', self.path, 'claim', '--lane', 'a')
            run('--queue', self.path, 'done', '--id', tid, '--token', self.tok(tid), '--result', result)
        _, out, _ = run('--queue', self.path, 'list', '--result', 'blocked')
        self.assertEqual([l.split('\t')[0] for l in out.splitlines()], ['q1'])
        _, out, _ = run('--queue', self.path, 'list')
        self.assertEqual(len(out.splitlines()), 2)  # no filter: both rows
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run('--queue', self.path, 'list', '--result', 'maybe')  # not one of the results

    def test_docstring_list_usage_names_every_list_flag(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            Q.main(['list', '--help'])
        flags = set(re.findall(r'(--[a-z][a-z-]*)', out.getvalue())) - {'--help'}
        usage = next(l for l in Q.__doc__.splitlines() if l.strip().startswith('gwt-queue list'))
        self.assertTrue(flags)
        for flag in sorted(flags):
            self.assertIn('[' + flag + ' ', usage)  # the usage block in the module docstring names it too

    def test_ids_increase(self):
        self.add(); self.add(person='Jose Ruiz', query='jose ruiz 1880')
        self.assertEqual([r['id'] for r in self.rows()], ['q1', 'q2'])

    def test_dedupe_skips_open_duplicate(self):
        self.add()
        code, out, _ = self.add('--dedupe')
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows()), 1)
        self.assertTrue(out.startswith('q1\t'))

    def test_dedupe_skips_a_search_already_done(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'done', '--id', 'q1', '--token', self.tok('q1'), '--result', 'none')
        code, out, _ = self.add('--dedupe')
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows()), 1)
        self.assertTrue(out.startswith('q1\t'))

    def park(self, task_id='q1'):
        for _ in range(5):
            run('--queue', self.path, 'claim', '--lane', 'l1')
            run('--queue', self.path, 'fail', '--id', task_id, '--token', self.tok(task_id), '--backoff-minutes', '0')
        self.assertEqual(self.rows()[0]['status'], 'failed')

    def test_dedupe_skips_a_failed_task_unless_retry_failed(self):
        self.add()
        self.park()
        self.add('--dedupe')
        self.assertEqual(len(self.rows()), 1)
        code, out, _ = self.add('--dedupe', '--retry-failed')
        self.assertEqual(code, 0)
        self.assertEqual([r['status'] for r in self.rows()], ['failed', 'open'])

    def test_dedupe_hit_on_an_open_task_refreshes_note_and_priority(self):
        self.add('--note', 'first round', priority=10)
        self.add('--dedupe', '--note', 'second round', priority=90)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['note'], rows[0]['priority']), ('second round', '90'))

    def test_dedupe_hit_on_an_open_task_refreshes_the_query(self):
        self.add('--dedupe-key', 'i7:search:fs', query='Ana Peiro 1850')
        self.add('--dedupe-key', 'i7:search:fs', '--dedupe', query='Ana Peiro Toledo 1850 Villa')
        self.assertEqual(self.rows()[0]['query'], 'ana peiro toledo 1850 villa')

    def test_dedupe_hit_without_priority_keeps_the_old_one(self):
        self.add(priority=10)
        run('--queue', self.path, 'add', '--person', 'Ana Peiro Toledo', '--kind', 'sweep', '--source',
            'familysearch', '--query', 'ana peiro 1850', '--dedupe')
        self.assertEqual(self.rows()[0]['priority'], '10')

    def test_dedupe_key_ignores_the_query(self):
        self.add('--dedupe-key', 'i7:search:fs', query='Ana Peiro 1850')
        self.add('--dedupe-key', 'i7:search:fs', '--dedupe', query='Ana Peiro Toledo 1850 Villa')
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.add('--dedupe-key', 'i7:verify:fs', '--dedupe', query='Ana Peiro 1850')
        self.assertEqual(len(self.rows()), 2)

    def test_jsonl_adds_a_batch_under_one_write(self):
        lines = (json.dumps({'person': 'I1', 'label': 'Ana', 'query': 'ana 1850', 'priority': 90,
                             'dedupe_key': 'i1:search:'}) + '\n\n'
                 + json.dumps({'person': 'I2', 'kind': 'verify', 'priority': 5}) + '\n')
        code, out, _ = self.jsonl(lines, '--dedupe')
        self.assertEqual(code, 0)
        self.assertEqual([l.split('\t')[0] for l in out.splitlines()], ['q1', 'q2'])
        code, out, _ = self.jsonl(lines, '--dedupe')
        self.assertEqual([l.split('\t')[0] for l in out.splitlines()], ['q1', 'q2'])
        self.assertEqual(len(self.rows()), 2)

    def test_jsonl_bad_line_writes_nothing(self):
        bad = json.dumps({'person': 'I1'}) + '\n' + json.dumps({'kind': 'search'}) + '\n'
        code, _, err = self.jsonl(bad)
        self.assertEqual(code, 2)
        self.assertIn('line 2', err)
        self.assertEqual(self.rows(), [])
        code, _, err = self.jsonl(json.dumps({'person': 'I1', 'next_at': 'tomorrow'}) + '\n')
        self.assertEqual(code, 2)

    def jsonl(self, text, *extra):
        old = sys.stdin
        sys.stdin = io.StringIO(text)
        try:
            return run('--queue', self.path, 'add', '--jsonl', '-', *extra)
        finally:
            sys.stdin = old

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
        self.assertEqual(r['tries'], '0')

    def test_repeated_release_never_parks_a_task(self):
        self.add()
        for _ in range(8):  # a paused gate: claim, release, claim, release ...
            run('--queue', self.path, 'claim', '--lane', 'l1')
            run('--queue', self.path, 'release', '--id', 'q1', '--token', self.tok('q1'))
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'fail', '--id', 'q1', '--token', self.tok('q1'))
        r = self.rows()[0]
        self.assertEqual(r['status'], 'open')
        self.assertEqual(r['tries'], '1')

    def test_stale_claim_reopens(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'dead-lane')
        self.age_claim('q1')  # its lease ran out; another lane picks the task up
        code, out, _ = run('--queue', self.path, 'claim', '--lane', 'l2')
        self.assertEqual(code, 0)
        self.assertEqual(self.rows()[0]['lane'], 'l2')
        self.assertEqual(self.rows()[0]['tries'], '2')  # the dead attempt kept its try

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
                r['lease_until'] = (datetime.datetime.now(datetime.timezone.utc)
                                    - datetime.timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.write_rows(rows)

    def write_rows(self, rows, extra=''):
        with open(self.path, 'w') as f:
            f.write('\t'.join(Q.COLUMNS) + '\n')
            for r in rows:
                f.write('\t'.join(r[c] for c in Q.COLUMNS) + '\n')
            f.write(extra)

    def test_old_worker_cannot_finish_a_task_reassigned_after_its_claim_went_stale(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'old')
        old_token = self.tok('q1')
        self.age_claim('q1')
        code, _, _ = run('--queue', self.path, 'claim', '--lane', 'new')
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
        self.park()  # a parked task cannot be completed either
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

    def test_renew_keeps_a_long_claim_alive(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'slow', '--lease-minutes', '1')
        token = self.tok('q1')
        self.age_claim('q1', minutes=1)
        code, _, _ = run('--queue', self.path, 'renew', '--id', 'q1', '--token', token, '--lease-minutes', '60')
        self.assertEqual(code, 0)
        run('--queue', self.path, 'report')
        self.assertEqual(self.rows()[0]['status'], 'claimed')
        code, _, _ = run('--queue', self.path, 'done', '--id', 'q1', '--token', token, '--result', 'hit')
        self.assertEqual(code, 0)

    def test_report_never_revokes_a_claim_inside_its_lease(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1', '--lease-minutes', '600')
        rows = self.rows()
        rows[0]['claimed_at'] = '2000-01-01T00:00:00Z'  # an old claim, but its own lease still runs
        self.write_rows(rows)
        run('--queue', self.path, 'report'); run('--queue', self.path, 'stats')
        self.assertEqual(self.rows()[0]['status'], 'claimed')

    def test_a_task_that_kills_its_lane_parks_after_max_tries(self):
        self.add()
        for i in range(5):
            code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l%d' % i)
            self.assertEqual(code, 0)
            self.age_claim('q1')
        run('--queue', self.path, 'report')
        self.assertEqual(self.rows()[0]['status'], 'failed')
        code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l9')
        self.assertEqual(code, 1)

    def test_fail_backoff_counts_failures_not_releases(self):
        self.add()
        for _ in range(3):
            run('--queue', self.path, 'claim', '--lane', 'l1')
            run('--queue', self.path, 'release', '--id', 'q1', '--token', self.tok('q1'))
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'fail', '--id', 'q1', '--token', self.tok('q1'), '--backoff-minutes', '60')
        gap = (Q.parse_iso(self.rows()[0]['next_at']) - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        self.assertLess(gap, 3700)  # one failed attempt, not four claims

    def test_release_retry_after_holds_the_task_back(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'release', '--id', 'q1', '--token', self.tok('q1'), '--retry-after', '10')
        code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
        self.assertEqual(code, 1)
        self.assertEqual(self.rows()[0]['tries'], '0')

    def test_times_without_a_zone_read_as_utc(self):
        self.add()
        for value in ('2000-01-01', '2000-01-01T10:00:00'):
            rows = self.rows()
            rows[0]['next_at'] = value
            self.write_rows(rows)
            for cmd in ('report', 'stats'):
                code, _, _ = run('--queue', self.path, cmd)
                self.assertEqual(code, 0)
            code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
            self.assertEqual(code, 0, value)
            run('--queue', self.path, 'release', '--id', 'q1', '--token', self.tok('q1'))

    def test_next_at_is_stored_in_utc_and_garbage_is_refused(self):
        self.add('--next-at', '2030-01-01T10:00:00+02:00')
        self.assertEqual(self.rows()[0]['next_at'], '2030-01-01T08:00:00Z')
        with self.assertRaises(SystemExit):
            self.add('--next-at', 'tomorrow', person='Jose Ruiz')
        self.assertEqual(len(self.rows()), 1)

    def test_blank_lines_and_bad_tries_do_not_break_the_queue(self):
        self.add()
        rows = self.rows()
        rows[0]['tries'] = 'x'
        self.write_rows(rows, extra='\n\n')
        code, out, _ = run('--queue', self.path, 'stats')
        self.assertIn('rows\t1', out)
        code, _, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
        self.assertEqual(code, 0)
        code, _, _ = run('--queue', self.path, 'fail', '--id', 'q1', '--token', self.tok('q1'))
        self.assertEqual(code, 0)
        with open(self.path) as f:
            self.assertNotIn('\n\n', f.read())

    def test_equal_priority_goes_in_id_order_past_q9(self):
        for i in range(11):
            self.add(person='P%d' % i, query='q%d' % i)
        got = []
        for _ in range(11):
            code, out, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
            got.append(out.split('\t')[0])
        self.assertEqual(got, ['q%d' % i for i in range(1, 12)])

    def test_printed_row_matches_the_stored_row(self):
        code, out, _ = self.add('--note', 'two\tcells\nand a line')
        stored = self.rows()[0]
        self.assertEqual(out.rstrip('\n').split('\t'), [stored[c] for c in Q.COLUMNS])
        self.assertEqual(stored['note'], 'two cells and a line')

    def test_retry_failed_still_skips_done_tasks(self):
        self.add()
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'done', '--id', 'q1', '--token', self.tok('q1'), '--result', 'none')
        self.add('--dedupe', '--retry-failed')
        self.assertEqual(len(self.rows()), 1)

    def test_a_done_task_keeps_its_final_note_on_a_dedupe_hit(self):
        self.add('--note', 'seeded')
        run('--queue', self.path, 'claim', '--lane', 'l1')
        run('--queue', self.path, 'done', '--id', 'q1', '--token', self.tok('q1'), '--result', 'hit',
            '--note', 'found the act')
        self.add('--dedupe', '--note', 'seeded again', priority=50)
        r = self.rows()[0]
        self.assertEqual((len(self.rows()), r['note'], r['priority']), (1, 'found the act', '0'))

    def test_a_blocked_or_denied_done_task_can_be_seeded_again(self):
        for result in ('blocked', 'denied'):
            self.path = os.path.join(self.dir.name, result + '.tsv')
            self.add()
            run('--queue', self.path, 'claim', '--lane', 'l1')
            run('--queue', self.path, 'done', '--id', 'q1', '--token', self.tok('q1'), '--result', result)
            code, out, _ = self.add('--dedupe')
            self.assertTrue(out.startswith('q2\t'), result)
            self.assertEqual([r['status'] for r in self.rows()], ['done', 'open'])

    def test_dedupe_tells_apart_names_in_other_scripts(self):
        # The queue normalizes like gwt-ledger, so a query in Cyrillic keeps its words and two names differ.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ledger'))
        import gwt_ledger
        for text in ('Иван Петров, 1880', 'Ørsted', 'Peiró, JOSÉ «Pepe»', 'snake_case'):
            self.assertEqual(Q.normalize(text), gwt_ledger.normalize(text), text)
        self.add(person='Ivan Petrov', query='Иван Петров 1880')
        self.assertEqual(self.rows()[0]['query'], 'иван петров 1880')
        code, out, _ = self.add('--dedupe', person='Ivan Petrov', query='Мария Сидорова 1880')
        self.assertTrue(out.startswith('q2\t'))
        code, out, _ = self.add('--dedupe', person='Ivan Petrov', query='ИВАН петров, 1880')
        self.assertTrue(out.startswith('q1\t'))
        self.assertEqual(len(self.rows()), 2)

    def test_lease_minutes_must_be_at_least_one(self):
        self.add()
        for bad in ('0', '-5'):
            with self.assertRaises(SystemExit):
                run('--queue', self.path, 'claim', '--lane', 'l1', '--lease-minutes', bad)
        self.assertEqual(self.rows()[0]['status'], 'open')

    def test_jsonl_with_a_non_string_next_at_exits_2(self):
        for value in (20300101, ['2030-01-01'], {'at': 1}):
            code, _, err = self.jsonl(json.dumps({'person': 'I1', 'next_at': value}) + '\n')
            self.assertEqual(code, 2, value)
            self.assertIn('line 1', err)
        self.assertEqual(self.rows(), [])

    def test_a_blank_first_line_does_not_turn_the_header_into_a_task(self):
        self.add()
        with open(self.path) as f:
            body = f.read()
        with open(self.path, 'w') as f:
            f.write('\n' + body)
        code, out, _ = run('--queue', self.path, 'stats')
        self.assertIn('rows\t1', out)
        code, out, _ = run('--queue', self.path, 'claim', '--lane', 'l1')
        self.assertTrue(out.startswith('q1\t'))

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
