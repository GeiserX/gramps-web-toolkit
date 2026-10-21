#!/usr/bin/env python3
"""gwt-ledger: a shared "already searched" ledger for parallel research agents.

One TSV file, one row per search: who was searched, where, with what query, when, and what came of it.
Agents run `check` before a search and `add` after it, so two lanes never pay for the same search twice.

  gwt-ledger check --person KEY --source SRC --query "..."   exit 0 and print rows if done, 1 if not
  gwt-ledger add --person KEY --label "..." --source SRC --query "..." --result none [--evidence F] [--agent A]
  gwt-ledger list --person KEY [--source SRC]
  gwt-ledger due [--on YYYY-MM-DD]                           rows whose next_date has arrived (appeal windows, retries)
  gwt-ledger stats

The ledger path comes from --ledger, else $GWT_LEDGER, else ./buscado.tsv. Standard library only.
"""
import argparse, collections, datetime, difflib, fcntl, os, re, sys, unicodedata

COLUMNS = ('person_key', 'person_label', 'source', 'query', 'date', 'result', 'evidence', 'agent', 'next_date')
RESULTS = ('hit', 'none', 'partial', 'blocked', 'denied')
DEFAULT_THRESHOLD = 0.75


def normalize(text):
    """Lowercase, strip accents and punctuation, collapse spaces: «Peiró, José» -> «peiro jose»."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    return ' '.join(re.sub(r'[^0-9a-z]+', ' ', text).split())


def key(text):
    """Person keys and sources compare as slugs: «José Pérez» and «jose-perez» are the same person."""
    return normalize(text).replace(' ', '-')


def similarity(a, b):
    """Fuzzy match of a new query `a` against a logged query `b`: the best of word-set overlap,
    character ratio, and (for queries of three words or more) how much of `a` the logged query covers."""
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    covered = len(ta & tb) / len(ta) if len(ta) >= 3 else 0.0
    return max(jaccard, covered, difflib.SequenceMatcher(None, a, b).ratio())


def clean(value):
    return ' '.join(str(value or '').replace('\t', ' ').splitlines()).strip()


def read_rows(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f):
            parts = line.rstrip('\n').split('\t')
            if n == 0 and parts[0] == COLUMNS[0]:
                continue  # header; a ledger from before next_date has 8 columns and reads the same
            if len(parts) < len(COLUMNS):
                parts += [''] * (len(COLUMNS) - len(parts))
            rows.append(dict(zip(COLUMNS, parts)))
    return rows


def add_row(path, row):
    """Append one row under an exclusive lock, writing the header if the file is new or empty."""
    line = '\t'.join(clean(row.get(c)) for c in COLUMNS) + '\n'
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write('\t'.join(COLUMNS) + '\n')
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def find(rows, person=None, source=None, query=None, threshold=DEFAULT_THRESHOLD):
    """Rows for this person and source whose query is close enough, best match first."""
    pk, sk, q = key(person) if person else None, key(source) if source else None, normalize(query or '')
    out = []
    for r in rows:
        if pk and key(r['person_key']) != pk:
            continue
        if sk and key(r['source']) != sk:
            continue
        score = similarity(q, normalize(r['query'])) if q else 1.0
        if score >= threshold:
            out.append((score, r))
    out.sort(key=lambda x: -x[0])
    return out


def print_rows(scored, with_score=False):
    for score, r in scored:
        cells = [r[c] for c in COLUMNS]
        sys.stdout.write(('%.2f\t' % score if with_score else '') + '\t'.join(cells) + '\n')


def iso_date(value):
    """YYYY-MM-DD or empty; anything else is an argparse error, so `due` never compares a typo as text."""
    return datetime.date.fromisoformat(value).isoformat() if value else ''


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-ledger', description=__doc__.split('\n')[0])
    p.add_argument('--ledger', default=os.environ.get('GWT_LEDGER', 'buscado.tsv'))
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='has this search been done? exit 0 with the rows if so, 1 if not')
    c.add_argument('--person'); c.add_argument('--source'); c.add_argument('--query', required=True)
    c.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD)
    a = sub.add_parser('add', help='append a row (safe with concurrent writers)')
    a.add_argument('--person', required=True); a.add_argument('--label', default='')
    a.add_argument('--source', required=True); a.add_argument('--query', required=True)
    a.add_argument('--result', required=True, choices=RESULTS)
    a.add_argument('--evidence', default=''); a.add_argument('--agent', default=os.environ.get('GWT_AGENT', ''))
    a.add_argument('--date', default=datetime.date.today().isoformat())
    a.add_argument('--next-date', default='', type=iso_date, help='ISO date to look again, e.g. the end of an appeal window')
    d = sub.add_parser('due', help='rows whose next_date is on or before a day (default today)')
    d.add_argument('--on', default=datetime.date.today().isoformat(), type=iso_date)
    l = sub.add_parser('list', help='rows for one person')
    l.add_argument('--person', required=True); l.add_argument('--source')
    sub.add_parser('stats', help='counts by result, source and agent')
    args = p.parse_args(argv)

    if args.cmd == 'add':
        add_row(args.ledger, {'person_key': key(args.person), 'person_label': args.label,
                              'source': key(args.source), 'query': normalize(args.query), 'date': args.date,
                              'result': args.result, 'evidence': args.evidence, 'agent': args.agent,
                              'next_date': args.next_date})
        return 0
    rows = read_rows(args.ledger)
    if args.cmd == 'check':
        scored = find(rows, args.person, args.source, args.query, args.threshold)
        print_rows(scored, with_score=True)
        return 0 if scored else 1
    if args.cmd == 'due':
        due = [(1.0, r) for r in rows if r['next_date'] and r['next_date'] <= args.on]
        due.sort(key=lambda x: x[1]['next_date'])
        print_rows(due)
        return 0
    if args.cmd == 'list':
        print_rows(find(rows, args.person, args.source))
        return 0
    print('rows\t%d' % len(rows))
    print('persons\t%d' % len({key(r['person_key']) for r in rows}))
    for col in ('result', 'source', 'agent'):
        for value, n in collections.Counter(r[col] for r in rows).most_common():
            print('%s\t%s\t%d' % (col, value or '-', n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
