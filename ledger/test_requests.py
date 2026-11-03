"""Requests to archives: due dates, the .ics export and costs. Every name and archive here is invented."""
import io, os, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_ledger as L


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = L.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


def unfold(text):
    """The calendar's logical lines: a CRLF followed by a space continues the line before (RFC 5545 3.1)."""
    return text.replace('\r\n ', '').split('\r\n')


class RequestsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.dir.name, 'buscado.tsv')
        self.base = ('--ledger', self.ledger)

    def tearDown(self):
        self.dir.cleanup()

    def request(self, *extra):
        code, out = run(*self.base, 'request', 'add', '--person', 'Ana Zorvalo', '--label', 'Ana Zorvalo',
                        '--archive', 'Parish of San Pedro', '--channel', 'email', *extra)
        self.assertEqual(code, 0)
        return out.strip()

    def test_ids_are_numbered_and_the_file_sits_beside_the_ledger(self):
        self.assertEqual([self.request(), self.request()], ['rq-1', 'rq-2'])
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, 'requests.tsv')))

    def test_update_changes_the_latest_state_and_needs_an_existing_id(self):
        rid = self.request('--next-date', '2026-11-20', '--cost', '10', '--currency', 'eur')
        self.assertEqual(run(*self.base, 'request', 'update', rid, '--status', 'answered', '--cost', '12.50')[0], 0)
        out = run(*self.base, 'request', 'list')[1].splitlines()
        self.assertEqual(len(out), 1)
        row = dict(zip(L.REQUEST_COLUMNS, out[0].split('\t')))
        self.assertEqual((row['status'], row['cost'], row['currency'], row['next_date']),
                         ('answered', '12.50', 'EUR', '2026-11-20'))
        self.assertEqual(run(*self.base, 'request', 'update', 'rq-99', '--status', 'closed')[0], 2)

    def test_due_lists_open_requests_whose_date_has_come(self):
        a = self.request('--next-date', '2026-11-10')
        b = self.request('--next-date', '2026-11-30')
        c = self.request('--next-date', '2026-11-05')
        run(*self.base, 'request', 'update', c, '--status', 'closed')
        out = run(*self.base, 'request', 'due', '--on', '2026-11-15')[1]
        self.assertEqual([l.split('\t')[0] for l in out.splitlines()], [a])
        self.assertNotIn(b, out)

    def test_ics_has_one_event_per_pending_date(self):
        self.request('--next-date', '2026-11-10', '--thread', 'mail; reply, please', '--cost', '5', '--currency', 'EUR')
        closed = self.request('--next-date', '2026-11-12')
        run(*self.base, 'request', 'update', closed, '--status', 'refused')
        run(*self.base, 'add', '--person', 'Luis Gil', '--source', 'boe', '--query', 'edicto 1902', '--result', 'denied',
            '--next-date', '2026-12-01')
        run(*self.base, 'add', '--person', 'Luis Gil', '--source', 'boe', '--query', 'edicto 1903', '--result', 'hit')
        code, out = run(*self.base, 'due', '--ics')
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith('\r\n'))
        lines = unfold(out)
        self.assertEqual((lines[0], lines[-2]), ('BEGIN:VCALENDAR', 'END:VCALENDAR'))
        self.assertEqual(lines.count('BEGIN:VEVENT'), 2)
        self.assertEqual(lines.count('END:VEVENT'), 2)
        self.assertIn('DTSTART;VALUE=DATE:20261110', lines)
        self.assertIn('DTEND;VALUE=DATE:20261111', lines)
        self.assertIn('UID:rq-1@gwt-ledger', lines)
        self.assertIn('DTSTART;VALUE=DATE:20261201', lines)
        self.assertTrue(any('mail\\; reply\\, please' in l for l in lines))  # escaped, RFC 5545 3.3.11
        self.assertTrue(all(len(l.encode('utf-8')) <= 75 for l in out.split('\r\n')))
        # --on bounds the export to that day.
        self.assertEqual(unfold(run(*self.base, 'due', '--ics', '--on', '2026-11-30')[1]).count('BEGIN:VEVENT'), 1)

    def test_long_lines_fold_without_splitting_a_character(self):
        line = 'DESCRIPTION:' + 'ñ' * 100
        folded = L.ics_fold(line)
        self.assertEqual(folded.replace('\r\n ', ''), line)
        self.assertTrue(all(len(part.encode('utf-8')) <= 75 for part in folded.split('\r\n')))

    def test_costs_total_per_payer_and_currency(self):
        self.request('--cost', '10', '--currency', 'EUR', '--payer', 'Zorvalo family')
        r = self.request('--cost', '4.50', '--currency', 'EUR', '--payer', 'Zorvalo family')
        self.request('--cost', '7', '--currency', 'USD', '--payer', 'Zorvalo family')
        self.request('--cost', '3', '--currency', 'EUR', '--payer', 'Gil family')
        self.request()  # no cost: not counted
        run(*self.base, 'request', 'update', r, '--cost', '5.50')
        out = run(*self.base, 'costs', '--by', 'payer')[1].splitlines()
        self.assertEqual(out, ['Gil family\tEUR\t3\t1', 'Zorvalo family\tEUR\t15.50\t2', 'Zorvalo family\tUSD\t7\t1'])

    def test_a_bad_cost_or_status_is_refused(self):
        for extra in (('--cost', 'ten'), ('--cost', '-3'), ('--status', 'lost'), ('--sent', '20/11/2026')):
            self.assertEqual(run(*self.base, 'request', 'add', '--person', 'x', '--archive', 'a', '--channel', 'post',
                                 *extra)[0], 2, extra)
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, 'requests.tsv')))

    def test_hand_edits_never_crash_costs_or_the_calendar(self):
        rid = self.request('--cost', '1e3', '--currency', 'eur', '--next-date', '2030-01-01')
        with open(os.path.join(self.dir.name, 'requests.tsv'), encoding='utf-8') as f:
            self.assertIn('\t1000\t', f.read())  # 1e3 stored as 1000
        with open(os.path.join(self.dir.name, 'requests.tsv'), 'a', encoding='utf-8') as f:
            f.write('rq-9\tx\t\tArchive\temail\t\t\t2030-13-01\t12,50\tEUR\t\tsent\t\t2026-01-01\n')
        code, out = run(*self.base, 'costs')
        self.assertEqual((code, out), (0, '-\tEUR\t1000\t1\n'))
        code, out = run(*self.base, 'due', '--ics')
        self.assertEqual(code, 0)
        self.assertEqual(out.count('BEGIN:VEVENT'), 1)
        self.assertIn('UID:%s@gwt-ledger' % rid, out)

    def test_update_refuses_the_header_word_as_an_id(self):
        self.request()
        self.assertEqual(run(*self.base, 'request', 'update', 'id', '--status', 'closed')[0], 2)


if __name__ == '__main__':
    unittest.main()
