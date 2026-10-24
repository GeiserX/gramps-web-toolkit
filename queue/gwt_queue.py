#!/usr/bin/env python3
"""gwt-queue: a durable work queue so parallel research agents know what to search next.

One TSV file, one row per task: who to research, what kind of task, where, with what query, at what
priority, and who claimed it. A planner (or gwt-leads) seeds the queue; every lane `claim`s its next
task, then `done` or `fail` it. A claim left stale by a dead lane reopens on its own.

  gwt-queue add --person KEY --label "..." --kind sweep --source SRC --query "..." --priority 100
  gwt-queue claim --lane LANE [--kind K] [--source S]   exit 0 and print the task (last column = its token), 1 if none is due
  gwt-queue done --id ID --token T --result hit [--evidence F]
  gwt-queue fail --id ID --token T [--note why]          back off, or park the task after --max-tries
  gwt-queue release --id ID --token T                    give a claimed task back, no penalty
  gwt-queue list [--status S] [--person KEY] [--lane L]
  gwt-queue report [--limit N]                           the open-leads report: what is due, by priority
  gwt-queue stats

The queue path comes from --queue, else $GWT_QUEUE, else ./cola.tsv. Standard library only.
"""
import argparse, collections, datetime, fcntl, os, re, sys, unicodedata, uuid

COLUMNS = ('id', 'person_key', 'person_label', 'kind', 'source', 'query', 'priority', 'status',
           'lane', 'claimed_at', 'done_at', 'result', 'evidence', 'tries', 'next_at', 'note', 'token')
RESULTS = ('hit', 'none', 'partial', 'blocked', 'denied')  # same words as gwt-ledger
KINDS = ('search', 'sweep', 'verify', 'followup')
STATUS = ('open', 'claimed', 'done', 'failed')
DEFAULT_STALE_MINUTES = 120
DEFAULT_MAX_TRIES = 5
DEFAULT_BACKOFF_MINUTES = 60


def normalize(text):
    """Lowercase, strip accents and punctuation, collapse spaces: «Peiró, José» -> «peiro jose»."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    return ' '.join(re.sub(r'[^0-9a-z]+', ' ', text).split())


def key(text):
    return normalize(text).replace(' ', '-')


def clean(value):
    return ' '.join(str(value or '').replace('\t', ' ').splitlines()).strip()


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(value):
    """ISO instant as written by now_iso (or a bare date); empty or invalid -> None."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            return datetime.datetime.fromisoformat(value + 'T00:00:00+00:00')
        except ValueError:
            return None


def parse_rows(text):
    rows = []
    for n, line in enumerate(text.splitlines()):
        parts = line.split('\t')
        if n == 0 and parts[0] == COLUMNS[0]:
            continue  # header; a file from before `token` has one column fewer and reads the same
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


def reopen_stale(rows, stale_minutes):
    """Claims older than stale_minutes go back to open: their lane died without done/fail."""
    now = datetime.datetime.now(datetime.timezone.utc)
    n = 0
    for r in rows:
        if r['status'] == 'claimed':
            claimed = parse_iso(r['claimed_at'])
            if claimed and (now - claimed).total_seconds() > stale_minutes * 60:
                r['status'], r['lane'], r['claimed_at'], r['token'] = 'open', '', '', ''
                n += 1
    return n


def print_row(r):
    sys.stdout.write('\t'.join(r.get(c, '') for c in COLUMNS) + '\n')


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
    """Higher priority first, then the older task; a negative priority never beats zero."""
    try:
        p = int(r['priority'])
    except ValueError:
        p = 0
    return (-p, r['id'])


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
    return parse_iso(value).strftime('%Y-%m-%dT%H:%M:%SZ') if parse_iso(value) else ''


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-queue', description=__doc__.split('\n')[0])
    p.add_argument('--queue', default=os.environ.get('GWT_QUEUE', 'cola.tsv'))
    p.add_argument('--stale-minutes', type=int, default=DEFAULT_STALE_MINUTES,
                   help='a claim this old reopens on the next claim/report (default %(default)s)')
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('add', help='seed a task (safe with concurrent writers)')
    a.add_argument('--person', required=True); a.add_argument('--label', default='')
    a.add_argument('--kind', default='search', choices=KINDS)
    a.add_argument('--source', default=''); a.add_argument('--query', default='')
    a.add_argument('--priority', type=int, default=0,
                   help='higher goes first; suggested: 100 direct ancestor, 50 their sibling, 10 collateral')
    a.add_argument('--note', default=''); a.add_argument('--next-at', default='', type=iso_or_empty,
                   help='not claimable before this ISO instant, e.g. a rate-limit cool-down')
    a.add_argument('--dedupe', action='store_true',
                   help='skip if an open or claimed task already has this person+kind+source+query')

    c = sub.add_parser('claim', help='take the best due task for a lane; exit 1 if none is due')
    c.add_argument('--lane', required=True, help='lane name; recorded on the row')
    c.add_argument('--kind', choices=KINDS); c.add_argument('--source')

    d = sub.add_parser('done', help='mark a claimed task finished')
    d.add_argument('--id', required=True); d.add_argument('--token', required=True, help='the last column `claim` printed'); d.add_argument('--result', required=True, choices=RESULTS)
    d.add_argument('--evidence', default=''); d.add_argument('--note', default='')

    f = sub.add_parser('fail', help='the task could not run now: back it off, or park it for good')
    f.add_argument('--id', required=True); f.add_argument('--token', required=True, help='the last column `claim` printed'); f.add_argument('--note', default='')
    f.add_argument('--backoff-minutes', type=int, default=DEFAULT_BACKOFF_MINUTES)
    f.add_argument('--max-tries', type=int, default=DEFAULT_MAX_TRIES,
                   help='after this many claims the task becomes failed instead of reopening')

    r = sub.add_parser('release', help='give a claimed task back to open, no penalty')
    r.add_argument('--id', required=True); r.add_argument('--token', required=True, help='the last column `claim` printed')

    l = sub.add_parser('list', help='rows, optionally filtered')
    l.add_argument('--status', choices=STATUS); l.add_argument('--person'); l.add_argument('--lane')

    rep = sub.add_parser('report', help='open leads: due tasks by priority, then not-yet-due and failed')
    rep.add_argument('--limit', type=int, default=20)

    sub.add_parser('stats', help='counts by status, kind, source and lane')
    args = p.parse_args(argv)

    if args.cmd == 'add':
        def add_fn(rows):
            if args.dedupe and any(r['status'] in ('open', 'claimed') and key(r['person_key']) == key(args.person)
                                   and r['kind'] == args.kind and key(r['source']) == key(args.source)
                                   and normalize(r['query']) == normalize(args.query) for r in rows):
                print_row(next(r for r in rows if r['status'] in ('open', 'claimed')
                               and key(r['person_key']) == key(args.person) and r['kind'] == args.kind
                               and key(r['source']) == key(args.source) and normalize(r['query']) == normalize(args.query)))
                return rows, 0
            row = {'id': new_id(rows), 'person_key': key(args.person), 'person_label': args.label,
                   'kind': args.kind, 'source': key(args.source), 'query': normalize(args.query),
                   'priority': str(args.priority), 'status': 'open', 'lane': '', 'claimed_at': '',
                   'done_at': '', 'result': '', 'evidence': '', 'tries': '0',
                   'next_at': args.next_at, 'note': args.note, 'token': ''}
            rows.append(row)
            print_row(row)
            return rows, 0
        return with_queue(args.queue, add_fn)

    if args.cmd == 'claim':
        def claim_fn(rows):
            reopen_stale(rows, args.stale_minutes)
            now = datetime.datetime.now(datetime.timezone.utc)
            due = sorted((r for r in rows if claimable(r, now, args.kind, args.source)), key=by_priority)
            if not due:
                return rows, 1
            r = due[0]
            r['status'], r['lane'], r['claimed_at'] = 'claimed', args.lane, now_iso()
            r['token'] = uuid.uuid4().hex[:12]
            r['tries'] = str(int(r['tries'] or '0') + 1)
            print_row(r)
            return rows, 0
        return with_queue(args.queue, claim_fn)

    if args.cmd == 'done':
        def done_fn(rows):
            r, why = owned(rows, args.id, args.token, 'done')
            if r is None:
                sys.stderr.write('gwt-queue: %s\n' % why)
                return rows, 2
            r['status'], r['done_at'], r['result'], r['token'] = 'done', now_iso(), args.result, ''
            if args.evidence:
                r['evidence'] = args.evidence
            if args.note:
                r['note'] = args.note
            print_row(r)
            return rows, 0
        return with_queue(args.queue, done_fn)

    if args.cmd == 'fail':
        def fail_fn(rows):
            r, why = owned(rows, args.id, args.token, 'fail')
            if r is None:
                sys.stderr.write('gwt-queue: %s\n' % why)
                return rows, 2
            tries = int(r['tries'] or '0')
            if tries >= args.max_tries:
                r['status'] = 'failed'
                r['next_at'] = ''
            else:
                r['status'] = 'open'
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                    minutes=args.backoff_minutes * tries)
                r['next_at'] = until.strftime('%Y-%m-%dT%H:%M:%SZ')
            r['lane'], r['claimed_at'], r['token'] = '', '', ''
            if args.note:
                r['note'] = args.note
            print_row(r)
            return rows, 0
        return with_queue(args.queue, fail_fn)

    if args.cmd == 'release':
        def release_fn(rows):
            r, why = owned(rows, args.id, args.token, 'release')
            if r is None:
                sys.stderr.write('gwt-queue: %s\n' % why)
                return rows, 2
            r['status'], r['lane'], r['claimed_at'], r['token'] = 'open', '', '', ''
            # the claim counted as a try, but nothing ran: give it back so pauses cannot park a task
            r['tries'] = str(max(0, int(r['tries'] or '0') - 1))
            print_row(r)
            return rows, 0
        return with_queue(args.queue, release_fn)

    # read-only commands: a stale claim is reopened and saved first, so report and stats tell the truth
    holder = {'rows': []}

    def read_only(rows):
        reopen_stale(rows, args.stale_minutes)
        holder['rows'] = rows
        return rows, 0
    if os.path.exists(args.queue):
        with_queue(args.queue, read_only, write=args.cmd in ('report', 'stats'))
    rows = holder['rows']

    if args.cmd == 'list':
        for r in rows:
            if args.status and r['status'] != args.status:
                continue
            if args.person and key(r['person_key']) != key(args.person):
                continue
            if args.lane and r['lane'] != args.lane:
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
