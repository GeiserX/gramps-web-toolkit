#!/usr/bin/env python3
"""gwt-queue: a durable work queue so parallel research agents know what to search next.

One TSV file, one row per task: who to research, what kind of task, where, with what query, at what
priority, and who claimed it. A planner (or gwt-leads) seeds the queue; every lane `claim`s its next
task, then `done` or `fail` it. Every claim carries a lease; a claim whose lease ran out (its lane died)
reopens on its own, and counts as a failed attempt.

  gwt-queue add --person KEY --label "..." --kind sweep --source SRC --query "..." --priority 100
  gwt-queue add --jsonl - [--dedupe]                     many tasks, one per JSON line, under one lock and one write
  gwt-queue claim --lane LANE [--kind K] [--source S]   exit 0 and print the task (column 17 = its token), 1 if none is due
  gwt-queue renew --id ID --token T                      push the lease out again during a long task
  gwt-queue done --id ID --token T --result hit [--evidence F]
  gwt-queue fail --id ID --token T [--note why]          back off, or park the task after --max-tries attempts
  gwt-queue release --id ID --token T [--retry-after M]  nothing ran (a gate pause, a shutdown): give the try back
  gwt-queue list [--status S] [--person KEY] [--lane L] [--result R]
  gwt-queue report [--limit N]                           the open-leads report: what is due, by priority
  gwt-queue stats

The queue path comes from --queue, else $GWT_QUEUE, else ./cola.tsv. Keep the file untracked and on one
host: every command rewrites it whole under a local flock, which does not hold across clones or machines.
Standard library only.
"""
import argparse, collections, datetime, fcntl, json, os, re, sys, unicodedata, uuid

COLUMNS = ('id', 'person_key', 'person_label', 'kind', 'source', 'query', 'priority', 'status',
           'lane', 'claimed_at', 'done_at', 'result', 'evidence', 'tries', 'next_at', 'note', 'token',
           'lease_until', 'dedupe_key')
RESULTS = ('hit', 'none', 'partial', 'blocked', 'denied')  # same words as gwt-ledger
SEARCHED = ('hit', 'none', 'partial')  # results of a search that ran
KINDS = ('search', 'sweep', 'verify', 'followup')
STATUS = ('open', 'claimed', 'done', 'failed')
DEFAULT_LEASE_MINUTES = 120
DEFAULT_MAX_TRIES = 5
DEFAULT_BACKOFF_MINUTES = 60


def normalize(text):
    """Casefold, strip accents and punctuation, collapse spaces: «Peiró, José» -> «peiro jose». Letters and
    digits of every script stay (Иван -> иван, Ørsted -> ørsted); the underscore separates words."""
    text = unicodedata.normalize('NFKD', (text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', text))


def key(text):
    return normalize(text).replace(' ', '-')


def clean(value):
    return ' '.join(str(value or '').replace('\t', ' ').splitlines()).strip()


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def utc_iso(when):
    return when.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(value):
    """ISO instant or bare date, read as UTC when it names no zone; empty or invalid -> None."""
    if not value:
        return None
    try:
        when = datetime.datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return when.replace(tzinfo=datetime.timezone.utc) if when.tzinfo is None else when


def as_int(value):
    """A number from the file; a hand-edited value that is not one counts as 0 instead of stopping every lane."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_rows(text):
    rows = []
    first = True
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        if first and parts[0] == COLUMNS[0]:
            first = False
            continue  # the header: the first line that is not blank
        first = False
        if len(parts) < len(COLUMNS):
            parts += [''] * (len(COLUMNS) - len(parts))
        rows.append(dict(zip(COLUMNS, parts)))
    return rows


def read_file(path):
    try:
        with open(path, encoding='utf-8') as f:
            return parse_rows(f.read())
    except FileNotFoundError:
        return []


def write_rows(path, rows):
    """Replace the file atomically: write a temp file next to it, fsync, rename over it, fsync the directory.
    A crash leaves the old queue or the new one, never half of either."""
    directory = os.path.dirname(os.path.abspath(path))
    tmp = '%s.tmp.%d' % (os.path.abspath(path), os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\t'.join(COLUMNS) + '\n')
        for r in rows:
            f.write('\t'.join(clean(r.get(c)) for c in COLUMNS) + '\n')
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, path)
    except OSError:
        os.unlink(tmp)
        raise
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def with_queue(path, fn, write=True):
    """Run fn(rows) under an exclusive lock on <queue>.lock (a file that is never replaced, so the lock
    survives the atomic rename) and save the rows; fn returns (rows, exit_code)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path + '.lock', 'a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            rows, code = fn(read_file(path))
            if write:
                write_rows(path, rows)
            return code
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def new_id(rows):
    """q1, q2, ... one above the highest number in use, so a human can say 'claim q7' in a brief."""
    top = 0
    for r in rows:
        if r['id'].startswith('q') and r['id'][1:].isdigit():
            top = max(top, int(r['id'][1:]))
    return 'q%d' % (top + 1)


def lease_end(r):
    """When the claim on r runs out: its own lease_until, else claimed_at plus the default lease."""
    until = parse_iso(r['lease_until'])
    if until:
        return until
    claimed = parse_iso(r['claimed_at'])
    return claimed + datetime.timedelta(minutes=DEFAULT_LEASE_MINUTES) if claimed else None


def reopen_stale(rows, max_tries):
    """Claims past their own lease go back to open: their lane died without done/fail/release. The dead
    attempt keeps its try, so a task that kills every lane that takes it parks as failed after max_tries."""
    now = datetime.datetime.now(datetime.timezone.utc)
    n = 0
    for r in rows:
        if r['status'] == 'claimed':
            end = lease_end(r)
            if end and now > end:
                r['status'] = 'failed' if as_int(r['tries']) >= max_tries else 'open'
                r['lane'], r['claimed_at'], r['token'], r['lease_until'] = '', '', '', ''
                n += 1
    return n


def print_row(r):
    sys.stdout.write('\t'.join(clean(r.get(c)) for c in COLUMNS) + '\n')


def claimable(r, now, kind=None, source=None):
    if r['status'] != 'open':
        return False
    due = parse_iso(r['next_at'])
    if due and due > now:
        return False
    if kind and r['kind'] != kind:
        return False
    if source and key(source) != key(r['source']):
        return False
    return True


def by_priority(r):
    """Higher priority first, then the older task (q2 before q10); a negative priority never beats zero."""
    m = re.fullmatch(r'q(\d+)', r['id'])
    return (-as_int(r['priority']), 0 if m else 1, int(m.group(1)) if m else 0, r['id'])


def owned(rows, task_id, token, action):
    """The row if it is claimed and `token` is the one its current claim was given; else (None, message).
    A lane whose claim went stale and was handed to another lane holds a dead token and is refused."""
    r = find_row(rows, task_id)
    if r is None:
        return None, 'no task %s' % task_id
    if r['status'] != 'claimed':
        return None, '%s is %s, not claimed: cannot %s' % (task_id, r['status'], action)
    if not token or r['token'] != token:
        return None, '%s is held under another claim (stale or wrong token): cannot %s' % (task_id, action)
    return r, ''


def find_row(rows, task_id):
    for r in rows:
        if r['id'] == task_id:
            return r
    return None


def iso_or_empty(value):
    """An --next-at value as a UTC instant; a value that does not parse is an error, never 'now'."""
    if value is not None and not isinstance(value, str):
        raise argparse.ArgumentTypeError('not an ISO date or instant: %r' % (value,))
    if not (value or '').strip():
        return ''
    when = parse_iso(value)
    if when is None:
        raise argparse.ArgumentTypeError('not an ISO date or instant: %r' % value)
    return utc_iso(when)


def at_least_one(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError('must be 1 or more, not %d' % n)
    return n


def dedupe_match(rows, row, retry_failed):
    """The existing row that stands for the same task, or None. A row matches on its dedupe_key when either
    side has one, else on person + kind + source + query. A done row matches when its search ran (result hit,
    none or partial); a done row that was blocked or denied does not stop a re-seed. Failed rows match too
    unless retry_failed."""
    statuses = ('open', 'claimed', 'done') if retry_failed else ('open', 'claimed', 'done', 'failed')
    for r in rows:
        if r['status'] not in statuses:
            continue
        if r['status'] == 'done' and r['result'] not in SEARCHED:
            continue
        if r['dedupe_key'] or row['dedupe_key']:
            if r['dedupe_key'] == row['dedupe_key']:
                return r
        elif (r['person_key'] == row['person_key'] and r['kind'] == row['kind'] and r['source'] == row['source']
              and normalize(r['query']) == row['query']):
            return r
    return None


def new_row(rows, person, label='', kind='search', source='', query='', priority=None, note='', next_at='',
            dedupe_key=''):
    if kind not in KINDS:
        raise ValueError('kind must be one of %s, not %r' % (', '.join(KINDS), kind))
    return {'id': new_id(rows), 'person_key': key(person), 'person_label': clean(label), 'kind': kind,
            'source': key(source), 'query': normalize(query),
            'priority': '' if priority is None else str(int(priority)), 'status': 'open',
            'lane': '', 'claimed_at': '', 'done_at': '', 'result': '', 'evidence': '', 'tries': '0',
            'next_at': iso_or_empty(next_at), 'note': clean(note), 'token': '', 'lease_until': '',
            'dedupe_key': clean(dedupe_key)}


def add_rows(rows, wanted, dedupe, retry_failed):
    """Append each wanted row unless dedupe finds it already queued. A hit on an open row refreshes its label,
    query, note and priority, so a re-seed carries the latest gaps instead of the first seed's. Returns the rows
    to print, one per wanted row."""
    shown = []
    for row in wanted:
        hit = dedupe_match(rows, row, retry_failed) if dedupe else None
        if hit is None:
            row['id'] = new_id(rows)
            row['priority'] = row['priority'] or '0'
            rows.append(row)
            shown.append(row)
            continue
        if hit['status'] == 'open':
            for col in ('person_label', 'query', 'priority', 'note'):
                if row[col]:
                    hit[col] = row[col]
        shown.append(hit)
    return shown


def read_jsonl(stream, rows):
    """Rows from JSON lines: {"person", "label", "kind", "source", "query", "priority", "note", "next_at",
    "dedupe_key"}; only person is required. A bad line stops the whole batch before anything is written."""
    out = []
    for n, line in enumerate(stream, 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict) or not str(obj.get('person') or '').strip():
                raise ValueError('needs an object with a "person"')
            out.append(new_row(rows, obj['person'], obj.get('label', ''), obj.get('kind', 'search'),
                               obj.get('source', ''), obj.get('query', ''), obj.get('priority'),
                               obj.get('note', ''), obj.get('next_at', ''), obj.get('dedupe_key', '')))
        except (ValueError, TypeError, argparse.ArgumentTypeError) as e:
            raise ValueError('line %d: %s' % (n, e))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-queue', description=__doc__.split('\n')[0])
    p.add_argument('--queue', default=os.environ.get('GWT_QUEUE', 'cola.tsv'))
    p.add_argument('--max-tries', type=int, default=DEFAULT_MAX_TRIES,
                   help='attempts not given back by release before a task parks as failed: a fail or a lease '
                        'that ran out each count one (default %(default)s)')
    sub = p.add_subparsers(dest='cmd', required=True)
    token_help = 'column 17 of the row `claim` printed'

    a = sub.add_parser('add', help='seed a task, or many with --jsonl (safe with concurrent writers)')
    a.add_argument('--person'); a.add_argument('--label', default='')
    a.add_argument('--kind', default='search', choices=KINDS)
    a.add_argument('--source', default=''); a.add_argument('--query', default='')
    a.add_argument('--priority', type=int,
                   help='higher goes first (default 0); suggested: 100 direct ancestor, 50 their sibling, 10 collateral')
    a.add_argument('--note', default=''); a.add_argument('--next-at', default='', type=iso_or_empty,
                   help='not claimable before this ISO instant (UTC when no zone is given), e.g. a cool-down')
    a.add_argument('--dedupe-key', default='',
                   help='match duplicates on this key instead of person+kind+source+query')
    a.add_argument('--jsonl', metavar='FILE',
                   help='read tasks as JSON lines from FILE (- for stdin) and add them under one lock')
    a.add_argument('--dedupe', action='store_true',
                   help='skip a task already queued, running or done (same dedupe key, else same '
                        'person+kind+source+query); an open match gets the new label, query, note and priority')
    a.add_argument('--retry-failed', action='store_true', help='with --dedupe, queue again a task that parked as failed')

    c = sub.add_parser('claim', help='take the best due task for a lane; exit 1 if none is due')
    c.add_argument('--lane', required=True, help='lane name; recorded on the row')
    c.add_argument('--kind', choices=KINDS); c.add_argument('--source')
    c.add_argument('--lease-minutes', type=at_least_one, default=DEFAULT_LEASE_MINUTES,
                   help='the claim reopens if neither renewed nor finished by then (default %(default)s)')

    n = sub.add_parser('renew', help='push the lease of a claimed task out again')
    n.add_argument('--id', required=True); n.add_argument('--token', required=True, help=token_help)
    n.add_argument('--lease-minutes', type=at_least_one, default=DEFAULT_LEASE_MINUTES)

    d = sub.add_parser('done', help='mark a claimed task finished')
    d.add_argument('--id', required=True); d.add_argument('--token', required=True, help=token_help)
    d.add_argument('--result', required=True, choices=RESULTS)
    d.add_argument('--evidence', default=''); d.add_argument('--note', default='')

    f = sub.add_parser('fail', help='the attempt ran and could not finish: back it off, or park it for good')
    f.add_argument('--id', required=True); f.add_argument('--token', required=True, help=token_help)
    f.add_argument('--note', default='')
    f.add_argument('--backoff-minutes', type=int, default=DEFAULT_BACKOFF_MINUTES,
                   help='wait this many minutes per attempt so far before the task is due again')

    r = sub.add_parser('release', help='nothing ran (a gate pause, a shutdown): reopen the task and give its try back')
    r.add_argument('--id', required=True); r.add_argument('--token', required=True, help=token_help)
    r.add_argument('--retry-after', type=int, default=0, metavar='MINUTES',
                   help='not claimable again for this many minutes, so a paused lane does not re-claim it at once')

    l = sub.add_parser('list', help='rows, optionally filtered')
    l.add_argument('--status', choices=STATUS); l.add_argument('--person'); l.add_argument('--lane')
    l.add_argument('--result', choices=RESULTS, help='only rows closed with this result, e.g. blocked or denied')

    rep = sub.add_parser('report', help='open leads: due tasks by priority, then not-yet-due and failed')
    rep.add_argument('--limit', type=int, default=20)

    sub.add_parser('stats', help='counts by status, kind, source and lane')
    args = p.parse_args(argv)

    def refuse(rows, why):
        sys.stderr.write('gwt-queue: %s\n' % why)
        return rows, 2

    if args.cmd == 'add':
        if bool(args.jsonl) == bool(args.person):
            p.error('add needs exactly one of --person and --jsonl')
        if args.retry_failed and not args.dedupe:
            p.error('--retry-failed only means something with --dedupe')
        batch = None
        if args.jsonl:
            try:
                stream = sys.stdin if args.jsonl == '-' else open(args.jsonl, encoding='utf-8')
                with stream:
                    batch = stream.readlines()
            except OSError as e:
                p.error('cannot read %s: %s' % (args.jsonl, e))

        def add_fn(rows):
            try:
                if batch is not None:
                    wanted = read_jsonl(batch, rows)
                else:
                    wanted = [new_row(rows, args.person, args.label, args.kind, args.source, args.query,
                                      args.priority, args.note, args.next_at, args.dedupe_key)]
            except ValueError as e:
                return refuse(rows, str(e))
            for row in add_rows(rows, wanted, args.dedupe, args.retry_failed):
                print_row(row)
            return rows, 0
        return with_queue(args.queue, add_fn)

    if args.cmd == 'claim':
        def claim_fn(rows):
            reopen_stale(rows, args.max_tries)
            now = datetime.datetime.now(datetime.timezone.utc)
            due = sorted((r for r in rows if claimable(r, now, args.kind, args.source)), key=by_priority)
            if not due:
                return rows, 1
            r = due[0]
            r['status'], r['lane'], r['claimed_at'] = 'claimed', clean(args.lane), utc_iso(now)
            r['lease_until'] = utc_iso(now + datetime.timedelta(minutes=args.lease_minutes))
            r['token'] = uuid.uuid4().hex[:12]
            r['tries'] = str(as_int(r['tries']) + 1)
            print_row(r)
            return rows, 0
        return with_queue(args.queue, claim_fn)

    if args.cmd == 'renew':
        def renew_fn(rows):
            r, why = owned(rows, args.id, args.token, 'renew')
            if r is None:
                return refuse(rows, why)
            r['lease_until'] = utc_iso(datetime.datetime.now(datetime.timezone.utc)
                                       + datetime.timedelta(minutes=args.lease_minutes))
            print_row(r)
            return rows, 0
        return with_queue(args.queue, renew_fn)

    if args.cmd == 'done':
        def done_fn(rows):
            r, why = owned(rows, args.id, args.token, 'done')
            if r is None:
                return refuse(rows, why)
            r['status'], r['done_at'], r['result'], r['token'], r['lease_until'] = 'done', now_iso(), args.result, '', ''
            if args.evidence:
                r['evidence'] = clean(args.evidence)
            if args.note:
                r['note'] = clean(args.note)
            print_row(r)
            return rows, 0
        return with_queue(args.queue, done_fn)

    if args.cmd == 'fail':
        def fail_fn(rows):
            r, why = owned(rows, args.id, args.token, 'fail')
            if r is None:
                return refuse(rows, why)
            tries = as_int(r['tries'])
            if tries >= args.max_tries:
                r['status'] = 'failed'
                r['next_at'] = ''
            else:
                r['status'] = 'open'
                r['next_at'] = utc_iso(datetime.datetime.now(datetime.timezone.utc)
                                       + datetime.timedelta(minutes=args.backoff_minutes * tries))
            r['lane'], r['claimed_at'], r['token'], r['lease_until'] = '', '', '', ''
            if args.note:
                r['note'] = clean(args.note)
            print_row(r)
            return rows, 0
        return with_queue(args.queue, fail_fn)

    if args.cmd == 'release':
        def release_fn(rows):
            r, why = owned(rows, args.id, args.token, 'release')
            if r is None:
                return refuse(rows, why)
            r['status'], r['lane'], r['claimed_at'], r['token'], r['lease_until'] = 'open', '', '', '', ''
            # the claim counted as a try, but nothing ran: give it back so pauses cannot park a task
            r['tries'] = str(max(0, as_int(r['tries']) - 1))
            r['next_at'] = utc_iso(datetime.datetime.now(datetime.timezone.utc)
                                   + datetime.timedelta(minutes=args.retry_after)) if args.retry_after > 0 else ''
            print_row(r)
            return rows, 0
        return with_queue(args.queue, release_fn)

    # read-only commands: a claim past its lease is reopened and saved first, so report and stats tell the truth
    holder = {'rows': []}

    def read_only(rows):
        reopen_stale(rows, args.max_tries)
        holder['rows'] = rows
        return rows, 0
    if os.path.exists(args.queue):
        with_queue(args.queue, read_only, write=args.cmd in ('report', 'stats'))
    rows = holder['rows']

    if args.cmd == 'list':
        for r in rows:
            if args.status and r['status'] != args.status:
                continue
            if args.person and r['person_key'] != key(args.person):
                continue
            if args.lane and r['lane'] != args.lane:
                continue
            if args.result and r['result'] != args.result:
                continue
            print_row(r)
        return 0

    if args.cmd == 'report':
        now = datetime.datetime.now(datetime.timezone.utc)
        due = sorted((r for r in rows if claimable(r, now)), key=by_priority)
        later = sorted((r for r in rows if r['status'] == 'open' and not claimable(r, now)), key=by_priority)
        failed = [r for r in rows if r['status'] == 'failed']
        print('due\t%d\t(claimable now)' % len(due))
        for r in due[:args.limit]:
            print_row(r)
        if len(due) > args.limit:
            print('...\t%d more due (raise --limit)' % (len(due) - args.limit))
        print('waiting\t%d\t(open, not yet due)' % len(later))
        print('failed\t%d\t(parked after max tries)' % len(failed))
        return 0

    print('rows\t%d' % len(rows))
    for col in ('status', 'kind', 'source', 'lane'):
        for value, n in collections.Counter(r[col] for r in rows if r['status'] != 'done' or col == 'status').most_common():
            print('%s\t%s\t%d' % (col, value or '-', n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
