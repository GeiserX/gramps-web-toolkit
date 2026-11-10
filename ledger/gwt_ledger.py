#!/usr/bin/env python3
"""gwt-ledger: a shared "already searched" ledger for parallel research agents.

One TSV file, one row per search: who was searched, where, with what query, when, and what came of it.
Agents run `check` before a search and `add` after it, so two lanes never pay for the same search twice.

  gwt-ledger check --person KEY --source SRC [--query "..."]  print matching rows; exit 0 if one is a search that ran
                                                             (hit, none, partial, out-of-coverage) and is not due
                                                             again, else 1 (--exact: same source, identical normalized
                                                             query; --any-result: any matching row counts)
  gwt-ledger check --jsonl FILE|-                             many checks, one JSON object per line, one read of the
                                                             ledger; one JSON answer per line with `done`, exit 0
  gwt-ledger add --person KEY --label "..." --source SRC --query "..." --result none --control-ok RUN [--evidence F]
  gwt-ledger void --person KEY --source SRC --query "..." --reason "..." [--label "..."]  retract the rows of that
                                                             exact search: check, due, stats and coverage ignore them
                                                             from now on (--label: default, the first row's label)
  gwt-ledger list --person KEY [--source SRC]                 every row, voided ones too, then its id and `voided`
  gwt-ledger due [--on YYYY-MM-DD] [--ics]                   rows whose next_date has arrived; --ics: a calendar
  gwt-ledger stats [--by source]
  gwt-ledger sources [--country XX]                          the source registry: ids, aliases, kinds, years
  gwt-ledger coverage --dump DUMP --sources FILE [--root I]  person x source matrix of what was searched
  gwt-ledger import-tsv FILE --map COL=FILECOL,... [--control-ok RUN]  append rows from another TSV log
  gwt-ledger scan add|check --source SRC --book ID --start N --end M [--person KEY] [--skip LIST]  images of a
                                                             book read (rows `<book> img N M`); check prints the gaps
  gwt-ledger request add|update|list|due ...                 letters and forms sent to archives
  gwt-ledger costs [--by payer|archive|person]               what the requests cost, per currency

Sources are canonical ids from ledger/sources.tsv (extend it with --registry or $GWT_LEDGER_SOURCES): `fs` is
logged and matched as `familysearch`. A source the registry does not know is kept as its slug, unless it is close to
a known name (familysea, gazeta-anexo): that is a typo or a drift, an error (exit 2) unless --new-source.
A `none` needs --control-ok (proof the source could have answered), or it is stored as `unproven`.

The ledger path comes from --ledger, else $GWT_LEDGER, else ./buscado.tsv. With --ledger-dir (or $GWT_LEDGER_DIR),
`add` writes to <dir>/<agent>.tsv, one file per lane, and every reader reads the ledger plus all <dir>/*.tsv.
A write never creates ./buscado.tsv on its own: with neither --ledger nor $GWT_LEDGER, and no --ledger-dir, add and
import-tsv stop (exit 2) when that file does not exist yet, so a logger run from the wrong folder cannot log into a
stray file. An existing ./buscado.tsv is still written; with --ledger-dir they write the lane file as before.
Standard library only (`coverage` also uses the toolkit's gwt_core).
"""
import argparse, collections, datetime, decimal, difflib, fcntl, glob, gzip, hashlib, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
COLUMNS = ('person_key', 'person_label', 'source', 'query', 'date', 'result', 'evidence', 'agent', 'next_date',
           'control')
RESULTS = ('hit', 'none', 'partial', 'blocked', 'denied', 'restricted', 'paid', 'in-person', 'out-of-coverage',
           'unproven')
# results of a search that ran; the rest did not answer it (blocked, denied, restricted, paid, in-person) or did
# not prove the source could have answered (unproven)
SEARCHED = ('hit', 'none', 'partial', 'out-of-coverage')
# Other words for a result, accepted by import-tsv only: `add` takes the exact words.
SYNONYMS = {'found': 'hit', 'positive': 'hit', 'match': 'hit', 'yes': 'hit',
            'negative': 'none', 'nothing': 'none', 'miss': 'none', 'not-found': 'none', 'notfound': 'none',
            'empty': 'none', 'no': 'none', 'partially': 'partial', 'block': 'blocked', 'captcha': 'blocked',
            'refused': 'denied', 'not-reviewed': 'unproven', 'unknown': 'unproven'}
DEFAULT_THRESHOLD = 0.75
# A `void` row retracts the rows it names (`voids <id> <id>; <reason>` in its evidence): `gwt-ledger void` writes it,
# never `add`. A row's id is the first 12 hex digits of the sha1 of its line and of how many identical lines come before
# it, so a void names exactly the rows that were in the ledger when it was written, and a new row of the same search,
# even one identical to a voided row, counts again.
VOID = 'void'
VOIDS = re.compile(r'^voids ((?:[0-9a-f]{12} ?)+);')

KINDS = ('index', 'image', 'fulltext', 'catalogue', 'newspaper')
REGISTRY_COLUMNS = ('id', 'aliases', 'country', 'kind', 'from', 'to')
SUFFIXES = ('verify', 'sweep', 'followup')  # task kinds of the research loop: <source>-verify and so on
ANY = 'any'                                 # the research loop's source for a task with no source
# the wheel's own name first: an installed copy sits in site-packages, where another package may own sources.tsv
SHIPPED_REGISTRY = (os.path.join(HERE, 'gwt_ledger_sources.tsv'), os.path.join(HERE, 'sources.tsv'))
NEAR_CUTOFF = 0.8  # an unknown source this close to a known name is a typo or a drift, not a new source

REQUEST_COLUMNS = ('id', 'person_key', 'person_label', 'archive', 'channel', 'thread', 'sent', 'next_date', 'cost',
                   'currency', 'payer', 'status', 'note', 'date')
STATUSES = ('planned', 'sent', 'answered', 'paid', 'closed', 'refused')
CLOSED = ('closed', 'refused')


def normalize(text):
    """Casefold, strip accents and punctuation, collapse spaces: «Peiró, José» -> «peiro jose». Letters and
    digits of every script stay (Иван -> иван, Ørsted -> ørsted); the underscore separates words."""
    text = unicodedata.normalize('NFKD', (text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', text))


def key(text):
    """Person keys and sources compare as slugs: «José Pérez» and «jose-perez» are the same person."""
    return normalize(text).replace(' ', '-')


# Particles that rewording drops or adds without changing the search.
PARTICLES = frozenset('a al con da de del di do du e el en la las le les los of the y'.split())


def one_edit(a, b):
    """True if `a` becomes `b` with at most one insertion, deletion or substitution."""
    if abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i + 1:] or a[i + 1:] == b[i:] or a[i:] == b[i + 1:]


def words_covered(new, logged):
    """Every word of `new` matches its own word of `logged`: exactly, or (words of five letters or more,
    no digits) with one typo, Villanueba for Villanueva. One logged word never covers two new words."""
    free = set(logged)
    rest = []
    for w in new:
        if w in free:
            free.discard(w)
        else:
            rest.append(w)
    for w in rest:
        if len(w) < 5 or any(c.isdigit() for c in w):
            return False
        hit = next((x for x in sorted(free) if len(x) >= 5 and not any(c.isdigit() for c in x) and one_edit(w, x)), None)
        if hit is None:
            return False
        free.discard(hit)
    return True


def similarity(a, b):
    """Fuzzy match of a new query `a` against a logged query `b`: the best of word-set overlap,
    character ratio, and (for queries of three words or more) how much of `a` the logged query covers.
    Every word of `a` except particles must be in `b`, give or take a typo: a different name, place
    or year after a long shared prefix is a different search, however high the overlap."""
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    if not words_covered(ta - PARTICLES, tb):
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    covered = len(ta & tb) / len(ta) if len(ta) >= 3 else 0.0
    return max(jaccard, covered, difflib.SequenceMatcher(None, a, b).ratio())


def clean(value):
    return ' '.join(str(value or '').replace('\t', ' ').splitlines()).strip()


# ---------------------------------------------------------------- the source registry


class Registry:
    """Canonical source ids with their aliases, country, kind and covered years. `lookup` answers the
    canonical id of any alias (or None); a row whose id ends in `-*` covers every id with that prefix."""

    def __init__(self):
        self.sources = {}   # id -> row
        self.alias = {}     # slug -> id (an id is its own alias)
        self.prefixes = []  # (prefix-, id) of the `-*` rows

    def load(self, path):
        with open(path, encoding='utf-8-sig') as f:  # -sig: a spreadsheet's UTF-8 export starts with a BOM
            for n, line in enumerate(f, 1):
                parts = [p.strip() for p in line.rstrip('\n').split('\t')]
                if not parts[0] or parts[0].startswith('#') or parts[0] == 'id':
                    continue
                row = dict(zip(REGISTRY_COLUMNS, parts + [''] * (len(REGISTRY_COLUMNS) - len(parts))))
                problem = self._add(row)
                if problem:
                    raise ValueError('%s:%d: %s' % (path, n, problem))
        return self

    def _add(self, row):
        sid = row['id']
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*(?:-\*)?', sid):
            return 'id %r is not a lowercase slug' % sid
        if row['kind'] not in KINDS and not (sid in self.sources and not row['kind']):  # extending keeps the kind
            return 'kind %r of %s is not one of %s' % (row['kind'], sid, ', '.join(KINDS))
        for col in ('from', 'to'):
            if row[col] and not re.fullmatch(r'\d{3,4}', row[col]):
                return '%s %r of %s is not a year' % (col, row[col], sid)
        old = self.sources.get(sid)
        if old:  # a user file extends a shipped row: its aliases add up, its other fields replace when set
            row = dict(old, **{k: v for k, v in row.items() if v and k != 'aliases'},
                       aliases=','.join(filter(None, [old['aliases'], row['aliases']])))
        self.sources[sid] = row
        if sid.endswith('-*'):
            self.prefixes.append((sid[:-1], sid))
            return None
        for a in [sid] + [key(a) for a in row['aliases'].split(',') if key(a)]:
            if self.alias.get(a, sid) != sid:
                return 'alias %r of %s already names %s' % (a, sid, self.alias[a])
            self.alias[a] = sid
        return None

    def lookup(self, source):
        """The canonical id of a source, alias or `<source>-verify|sweep|followup`; None when unknown."""
        s = key(source)
        if not s:
            return None
        if s == ANY:
            return ANY
        if s in self.alias:
            return self.alias[s]
        for prefix, _ in self.prefixes:
            if s.startswith(prefix) and len(s) > len(prefix):
                return s
        base, _, suffix = s.rpartition('-')
        if base and suffix in SUFFIXES:
            canon = self.lookup(base)
            if canon:
                return canon + '-' + suffix
        return None

    def near(self, source):
        """Known names (ids and aliases) close enough to an unknown source to be a typo or a spelling drift."""
        s = key(source)
        base, _, suffix = s.rpartition('-')
        if base and suffix in SUFFIXES:
            s = base
        return difflib.get_close_matches(s, list(self.alias), n=3, cutoff=NEAR_CUTOFF)

    def resolve(self, source, new_source=False):
        """(id, problem). A known source or alias gives its id; an unknown one far from every known name is kept
        as its slug. One close to a known name is refused (the problem names the match) unless new_source."""
        sid = self.lookup(source)
        if sid:
            return sid, None
        slug = key(source)
        if not slug:
            return None, 'an empty source'
        near = [] if new_source else self.near(slug)
        if near:
            return None, ('%r is not in the registry but close to %s: a typo or another spelling of a known source. '
                          'Use that name, add yours as its alias in a --registry file, or pass --new-source if it '
                          'is a different source' % (source, ', '.join(near)))
        return slug, None

    def canon(self, source):
        """lookup(), or the slug itself for a source the registry does not know (a row logged with --new-source)."""
        return self.lookup(source) or key(source)

    def row(self, sid):
        """The registry row of a canonical id, through its `-*` family when it has one."""
        if sid in self.sources:
            return self.sources[sid]
        return next((self.sources[r] for p, r in self.prefixes if sid.startswith(p)), None)


def load_registry(user=''):
    """The shipped registry, extended by the user's file when given."""
    reg = Registry()
    shipped = next((p for p in SHIPPED_REGISTRY if os.path.exists(p)), None)
    if not shipped:
        raise ValueError('the shipped source registry is missing (looked for %s)' % ' and '.join(SHIPPED_REGISTRY))
    reg.load(shipped)
    if user:
        reg.load(user)
    return reg


# ---------------------------------------------------------------- the ledger file


def read_rows(path, columns=COLUMNS):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f):
            parts = line.rstrip('\n').split('\t')
            if n == 0 and parts[0] == columns[0]:
                continue  # header; a ledger from before next_date or control has fewer columns and reads the same
            if len(parts) < len(columns):
                parts += [''] * (len(columns) - len(parts))
            row = dict(zip(columns, parts))
            if columns == COLUMNS:  # a none from before 1.1.0 has no control: it reads as unproven, as add stores it
                row['line'] = '\t'.join(parts[:len(columns)])  # the id hashes the line as written, never the mapped word
                row['at'] = '%s:%d' % (path, n + 1)
                result = row['result']
                if result not in RESULTS:  # a hand-written word reads as import-tsv would store it; void stays void
                    word = key(result)
                    result = word if word in RESULTS else SYNONYMS.get(word, result)
                row['result'] = proven(result, row['control'])
            rows.append(row)
    return rows


def with_ids(rows):
    """Give each row its id, in reading order: the main ledger, then the lane files by name."""
    seen = collections.Counter()
    for r in rows:
        seen[r['line']] += 1
        r['id'] = hashlib.sha1(('%s\t%d' % (r['line'], seen[r['line']])).encode('utf-8')).hexdigest()[:12]
    return rows


def retracted(rows):
    """Give the rows their ids and return the ids the void rows among them name."""
    with_ids(rows)
    gone = set()
    for r in rows:
        m = VOIDS.match(r['evidence']) if r['result'] == VOID else None
        if m:
            gone.update(m.group(1).split())
    return gone


def next_day(r):
    """The row's next_date when it is a YYYY-MM-DD date, else '': text a hand edit put there is never due."""
    value = r['next_date']
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return ''
    try:
        datetime.date.fromisoformat(value)  # 2026-02-30 has the shape and is no day
    except ValueError:
        return ''
    return value


def odd_rows(rows):
    """One note on stderr per kind of hand edit a reader cannot use: a result word outside the vocabulary (the row
    counts as not searched) and a next_date that is no date (the row is never due). Each names the first such row."""
    bad = [r for r in rows if r['result'] not in RESULTS]
    if bad:
        sys.stderr.write('gwt-ledger: %d row(s) have a result that is none of %s, so they count as not searched '
                         '(first: %s, %r)\n' % (len(bad), ', '.join(RESULTS), bad[0]['at'], bad[0]['result']))
    bad = [r for r in rows if r['next_date'] and not next_day(r)]
    if bad:
        sys.stderr.write('gwt-ledger: %d row(s) have a next_date that is not YYYY-MM-DD, so they are never due '
                         '(first: %s, %r)\n' % (len(bad), bad[0]['at'], bad[0]['next_date']))


def live(rows):
    """The rows that still count: no void rows, and none of the rows a void names."""
    gone = retracted(rows)
    lost = gone - {r['id'] for r in rows}
    if lost:  # an id is a hash of its line: an edited or copied line brings the row it voided back
        sys.stderr.write('gwt-ledger: %d void id(s) name no row (%s): a voided line was edited, copied or deleted, '
                         'so the row counts again\n' % (len(lost), ' '.join(sorted(lost))))
    return [r for r in rows if r['result'] != VOID and r['id'] not in gone]


def ledger_paths(ledger, ledger_dir=''):
    """The main ledger first, then every per-lane file of ledger_dir."""
    return [ledger] + (sorted(glob.glob(os.path.join(ledger_dir, '*.tsv'))) if ledger_dir else [])


def same_search(r, reg):
    """What makes two rows the same logged search: person, source, query, day and result."""
    return (key(r['person_key']), reg.canon(r['source']), normalize(r['query']), r['date'], r['result'])


def add_rows(path, rows, columns=COLUMNS, unless=None):
    """Append rows under an exclusive lock, writing the header if the file is new or empty. With unless, call it
    under the lock first and write nothing when it returns something true, which is then returned."""
    lines = ''.join('\t'.join(clean(row.get(c)) for c in columns) + '\n' for row in rows)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            found = unless() if unless else None
            if found:
                return found
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write('\t'.join(columns) + '\n')
            f.write(lines)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def add_row(path, row, unless=None):
    """Append one row under an exclusive lock, writing the header if the file is new or empty."""
    return add_rows(path, [row], unless=unless)


def log_search(path, row, reg, paths):
    """Append one search row unless a live row of any lane file is the same search (same_search)."""
    mine = same_search(row, reg)

    def twins():  # a logger run twice must not log every search twice; a voided row is no reason to skip
        rows = live([r for lp in paths for r in read_rows(lp)])
        return [r['id'] for r in rows if key(r['person_key']) == mine[0] and same_search(r, reg) == mine]

    # read under the lane file's lock, so two runs of one logger cannot both find no row and both write
    found = add_row(path, row, unless=twins)
    if found:
        sys.stderr.write('gwt-ledger: not added: the same search (person, source, query, date and result) is '
                         'already logged as %s\n' % ' '.join(found))
    return found


def find(rows, person=None, source=None, query=None, threshold=DEFAULT_THRESHOLD, exact=False, registry=None,
         newest_first=False):
    """Rows for this person and source whose query is close enough, best match first and, among equal
    scores, newest first (by date, then by position), so an old `none` cannot hide a newer `hit`.
    With exact=True only the identical normalized query counts. With a registry, sources compare by
    canonical id, so a row logged as `fs` answers a check for `familysearch-index`."""
    canon = registry.canon if registry else key
    pk, sk, q = key(person) if person else None, canon(source) if source else None, normalize(query or '')
    out = []
    for i, r in enumerate(rows):
        if pk and key(r['person_key']) != pk:
            continue
        if sk and canon(r['source']) != sk:
            continue
        if exact and normalize(r['query']) != q:
            continue
        score = 1.0 if exact or not q else similarity(q, normalize(r['query']))
        if exact or score >= threshold:
            out.append((score, r['date'], i, r))
    if q or newest_first:
        out.sort(key=lambda x: x[:3], reverse=True)
    return [(score, r) for score, _, _, r in out]


def print_rows(scored, with_score=False, columns=COLUMNS):
    for score, r in scored:
        cells = [r[c] for c in columns]
        sys.stdout.write(('%.2f\t' % score if with_score else '') + '\t'.join(cells) + '\n')


def check_search(rows, reg, person=None, source=None, query=None, threshold=DEFAULT_THRESHOLD, exact=False,
                 any_result=False, new_source=False, date='', by_person=None):
    """One `check`: (problem, scored rows, done). problem is the usage error, or None; done is True exactly when
    `check` exits 0. by_person (key -> that person's rows, in ledger order) narrows the rows find reads anyway."""
    for name, value in (('query', query), ('person', person)):
        if value is not None and not normalize(value):
            return '--%s %r: nothing left to match after normalising' % (name, value), None, None
    if exact and not key(source or ''):
        return 'check --exact needs a non-empty --source', None, None
    if query is None and not (person and key(source or '')):
        return 'check needs --query, or --person and --source to ask about any search of that source', None, None
    if source:
        source, problem = reg.resolve(source, new_source)
        if problem:
            return '--source ' + problem, None, None
    else:
        source = None
    if exact and query is None:
        return 'check --exact needs --query', None, None
    if by_person is not None and person:
        rows = by_person.get(key(person), [])
    scored = find(rows, person, source, query, threshold, exact, reg, newest_first=True)
    done = [r for _, r in scored if r['result'] in SEARCHED and (not next_day(r) or next_day(r) > date)]
    return None, scored, bool(scored if any_result else done)


CHECK_FIELDS = {'person': str, 'source': str, 'query': str, 'exact': bool, 'any_result': bool,
                'threshold': (int, float)}


def read_checks(stream):
    """[(line number, search)] from JSON lines, one object per line with the fields of CHECK_FIELDS; null is the
    same as a missing field. ValueError naming the line on the first bad one."""
    out = []
    for n, line in enumerate(stream, 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError('needs a JSON object')
            unknown = sorted(set(obj) - set(CHECK_FIELDS))
            if unknown:
                raise ValueError('unknown field(s) %s; a line takes %s' % (', '.join(unknown), ', '.join(CHECK_FIELDS)))
            for name, kind in CHECK_FIELDS.items():
                value = obj.get(name)
                if value is not None and (not isinstance(value, kind) or (kind != bool and isinstance(value, bool))):
                    raise ValueError('%s must be %s' % (name, {str: 'a string', bool: 'true or false'}.get(kind, 'a number')))
        except ValueError as e:
            raise ValueError('line %d: %s' % (n, e))
        out.append((n, obj))
    return out


def iso_date(value):
    """YYYY-MM-DD or empty; anything else is an argparse error, so `due` never compares a typo as text."""
    return datetime.date.fromisoformat(value).isoformat() if value else ''


def amount(value):
    """A cost: a plain decimal number, `12.50`. Anything else is an argparse error."""
    try:
        d = decimal.Decimal(value)
    except decimal.InvalidOperation:
        raise argparse.ArgumentTypeError('not a number: %r' % value)
    if not d.is_finite() or d < 0:
        raise argparse.ArgumentTypeError('not a cost: %r' % value)
    return format(d, 'f')  # 1e3 is stored as 1000


NOT_PROOF = frozenset('no n false 0 none fail failed void short blocked'.split())


def control_problem(ref):
    """Why `--control-ok REF` proves nothing, or None. A REF that is a file must be the saved output of
    `gwt read <source> control`: at least one `ok` control line and no `void` or `SHORT` line. A REF that looks
    like a path but is no file is a typo, never a run id."""
    if not clean(ref):
        return 'an empty --control-ok proves nothing'
    if key(ref) in NOT_PROOF or not normalize(ref):
        return '%r is not a control run' % ref
    if not os.path.isfile(ref):
        if '/' in ref or os.sep in ref or re.search(r'\.(txt|tsv|log|out|json)$', ref, re.I):
            return 'no such file: %s (a run id has no / and no file extension)' % ref
        return None  # a run id from the caller's own runner, stored as given
    with open(ref, encoding='utf-8', errors='replace') as f:
        lines = f.read().splitlines()
    oks = [l for l in lines if l.startswith('control\t') and l.endswith('\tok')]
    bad = [l for l in lines if l.startswith('void\t') or (l.startswith('control\t') and not l.endswith('\tok'))]
    if bad or not oks:
        return '%s is not a passing control run (%s)' % (ref, 'it says: ' + bad[0] if bad else 'no ok control line')
    return None


def proven(result, control):
    """`none` without proof the source could answer is stored as `unproven`, so the search runs again."""
    if result == 'none' and not control:
        return 'unproven'
    return result


# ---------------------------------------------------------------- requests to archives


def fold_requests(rows):
    """The latest state of each request: `request update` appends a row, and its non-empty fields win."""
    state = collections.OrderedDict()
    for r in rows:
        cur = state.setdefault(r['id'], dict(r))
        cur.update({k: v for k, v in r.items() if v})
    return list(state.values())


def add_request(path, row):
    """Append a request under the lock, numbering it rq-N after the highest id in the file. Returns its id."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'a+', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            lines = f.read().splitlines()
            ids = [l.split('\t', 1)[0] for l in lines[1 if lines and lines[0].startswith('id\t') else 0:]]
            if row.get('id'):  # an update: the request must exist
                if row['id'] not in ids:
                    return None
            else:
                row['id'] = 'rq-%d' % (max([int(i[3:]) for i in ids if re.fullmatch(r'rq-\d+', i)] or [0]) + 1)
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write('\t'.join(REQUEST_COLUMNS) + '\n')
            f.write('\t'.join(clean(row.get(c)) for c in REQUEST_COLUMNS) + '\n')
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return row['id']


# ---------------------------------------------------------------- calendar export


def ics_escape(text):
    return clean(text).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')


def ics_fold(line):
    """Lines of at most 75 octets, continued with a space, never splitting a UTF-8 character (RFC 5545 3.1)."""
    out, cur = [], b''
    for ch in line:
        b = ch.encode('utf-8')
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur)
            cur = b''
        cur += b
    out.append(cur)
    return '\r\n '.join(x.decode('utf-8') for x in out)


def ics(events):
    """A VCALENDAR of all-day VEVENTs from (uid, YYYY-MM-DD, summary, description)."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//gramps-web-toolkit//gwt-ledger//EN', 'CALSCALE:GREGORIAN']
    for uid, day, summary, desc in events:
        d = datetime.date.fromisoformat(day)
        lines += ['BEGIN:VEVENT', 'UID:%s@gwt-ledger' % uid, 'DTSTAMP:' + stamp,
                  'DTSTART;VALUE=DATE:' + d.strftime('%Y%m%d'),
                  'DTEND;VALUE=DATE:' + (d + datetime.timedelta(days=1)).strftime('%Y%m%d'),
                  'SUMMARY:' + ics_escape(summary), 'DESCRIPTION:' + ics_escape(desc), 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return ''.join(ics_fold(l) + '\r\n' for l in lines)


# ---------------------------------------------------------------- coverage matrix


def load_dump(path):
    """A `gwt-check dump` or a gwt-apply snapshot, gzipped or not, as a dict."""
    with open(path, 'rb') as f:
        gz = f.read(2) == b'\x1f\x8b'
    with (gzip.open if gz else open)(path, 'rt', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError('%s: not a gwt-check dump or a gwt-apply snapshot' % path)
    return data


def life_years(rules, ix, p):
    """(low, high): the years a person's records can fall in, from their own dated events; None when open.
    A birth gives the low end, a death the high end; a missing end is the other end -/+ 110 years, and with
    neither, any dated event of their own -/+ 110 years."""
    ev = ix.own_events(p, None)
    births = [first for t, _, first in ev if t in rules.BIRTH_TYPES and first]
    deaths = [late for t, late, _ in ev if t in rules.END_TYPES and late]
    other = [first for _, _, first in ev if first]
    lo = min(births) if births else None
    hi = max(deaths) if deaths else None
    if lo is None and hi is None and other:
        lo, hi = min(other) - rules.LIVING_YEARS, max(other) + rules.LIVING_YEARS
    if lo is None and hi is not None:
        lo = hi - rules.LIVING_YEARS
    if hi is None and lo is not None:
        hi = lo + rules.LIVING_YEARS
    return lo, hi


def out_of_years(src, lo, hi):
    """True when the source's covered years cannot hold a record of a life spanning lo..hi."""
    if not src or lo is None:
        return False
    return bool((src['to'] and int(src['to']) < lo) or (src['from'] and int(src['from']) > hi))


def cell(rows, today):
    """One matrix cell from the rows of one person and source: due, hit, else the newest row's result."""
    if not rows:
        return '-'
    newest = max(rows, key=lambda r: r['date'])
    if next_day(newest) and next_day(newest) <= today:
        return 'due'
    if any(r['result'] == 'hit' for r in rows):
        return 'hit'
    return newest['result'] or '?'


def coverage(args, reg, rows, err):
    try:
        from gwt_core import rules
    except ImportError:  # a clone: the core lives in ../core
        sys.path.insert(0, os.path.join(HERE, '..', 'core'))
        from gwt_core import rules
    try:
        ix = rules.Index(load_dump(args.dump))
        with open(args.sources, encoding='utf-8-sig') as f:
            wanted = [l.split('\t')[0].strip() for l in f if l.strip() and not l.startswith('#')]
    except (OSError, ValueError, EOFError) as exc:
        return err('cannot read the dump or the sources file: %s' % exc)
    cols = []
    for s in wanted:
        if s == 'id':
            continue  # a registry-shaped file's header
        if s.endswith('-*'):
            continue  # a registry family row: its members are columns only when named one by one
        c, problem = reg.resolve(s)
        if problem:
            return err('source %s (in %s)' % (problem, args.sources))
        if c not in cols:
            cols.append(c)
    by_id = {p.get('gramps_id'): h for h, p in ix.people.items()}
    if args.root:
        if args.root not in by_id:
            return err('--root %s is not in the dump' % args.root)
        order, seen = [by_id[args.root]], {by_id[args.root]}
        for h in order:  # the root, then its ancestors, generation by generation
            for ph in ix.handles(ix.people[h].get('parent_family_list'), 'parents'):
                if ph and ph not in seen:
                    seen.add(ph)
                    order.append(ph)
    else:
        order = sorted(ix.people, key=lambda h: str(ix.people[h].get('gramps_id') or ''))
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(key(r['person_key']), reg.canon(r['source']))].append(r)
    gids = {key(p.get('gramps_id') or '') for p in ix.people.values()} - {''}
    owned = collections.defaultdict(set)  # `i0042` -> {`i0042-ana-zorvalo`, ...}: keys that start with an id
    for k in {k for k, _ in groups}:
        parts = k.split('-')  # the longest id wins: `i0042-b-luis` is I0042-b's, never I0042's
        owner = next((g for g in ('-'.join(parts[:n]) for n in range(len(parts), 0, -1)) if g in gids), None)
        if owner:
            owned[owner].add(k)

    def label_of(p):
        name = p.get('primary_name') or {}
        surname = ' '.join(s.get('surname', '') for s in name.get('surname_list') or [])
        return ' '.join(filter(None, [name.get('first_name', ''), surname]))

    names = collections.Counter(key(label_of(p)) for p in ix.people.values())

    def keys_of(p):
        """A person's ledger keys: their Gramps id, any key that starts with `<id>-`, and their name as a slug
        when no one else in the tree has it, so namesakes never show each other's rows."""
        gid, name = key(p.get('gramps_id') or ''), key(label_of(p))
        out = {gid} | owned.get(gid, set()) if gid else set()
        if name and names[name] == 1:
            out.add(name)
        return out

    matched = set().union(*(keys_of(p) for p in ix.people.values())) if ix.people else set()
    unmatched = {k for k, _ in groups if k} - matched
    if unmatched:
        sys.stderr.write('gwt-ledger: %d of %d person keys of the ledger match nobody in the dump; they are matched '
                         'by Gramps id, by a key that starts with <id>-, or by a name only one person has\n'
                         % (len(unmatched), len({k for k, _ in groups if k})))
    today = datetime.date.today().isoformat()
    sys.stdout.write('\t'.join(['id', 'name'] + cols) + '\n')
    for h in order:
        p = ix.people[h]
        label = label_of(p)
        keys = keys_of(p)
        lo, hi = life_years(rules, ix, p)
        cells = []
        for c in cols:
            mine = [r for k in keys for r in groups.get((k, c), [])]
            cells.append(cell(mine, today) if mine else ('n/a' if out_of_years(reg.row(c), lo, hi) else '-'))
        sys.stdout.write('\t'.join([p.get('gramps_id') or '', label] + cells) + '\n')
    return 0


# ---------------------------------------------------------------- import


def import_tsv(args, reg, err):
    """Append the rows of another TSV log, mapped column by column. Every row is checked first: one
    unknown result or source, or a bad date, refuses the whole file and nothing is written."""
    mapping = {}
    for pair in args.map.split(','):
        col, _, src = pair.partition('=')
        if col.strip() not in COLUMNS or not src.strip():
            return err('--map %r: want LEDGER_COLUMN=FILE_COLUMN, ledger columns: %s' % (pair, ', '.join(COLUMNS)))
        mapping[col.strip()] = src.strip()
    for need in ('source', 'query', 'result'):
        if need not in mapping:
            return err('--map needs %s=<column of %s>' % (need, args.file))
    try:
        with open(args.file, encoding='utf-8-sig') as f:
            lines = [l.rstrip('\r\n').split('\t') for l in f]
    except (OSError, UnicodeDecodeError) as exc:
        return err('cannot read %s: %s' % (args.file, exc))
    header = lines[0] if lines else []
    missing = [c for c in mapping.values() if c not in header]
    if missing:
        return err('%s has no column %s (its header: %s)' % (args.file, ', '.join(missing), ', '.join(header)))
    idx = {col: header.index(src) for col, src in mapping.items()}
    out, problems, mapped, unproven, unregistered = [], [], collections.Counter(), 0, collections.Counter()
    checked, skipped = {}, 0

    def same(r):  # a row already in the ledger: importing the same file twice adds nothing
        return same_search(r, reg)

    try:
        seen = {same(r) for r in live([r for path in ledger_paths(args.ledger, args.ledger_dir)
                                         for r in read_rows(path)])}  # a voided row is no reason to skip
    except (OSError, UnicodeDecodeError) as exc:
        return err('cannot read the ledger: %s' % exc)
    for n, parts in enumerate(lines[1:], 2):
        if not any(p.strip() for p in parts):
            continue
        v = {col: parts[i].strip() if i < len(parts) else '' for col, i in idx.items()}
        word = key(v['result'])
        result = word if word in RESULTS else SYNONYMS.get(word)
        if result is None:
            problems.append('line %d: result %r is none of %s' % (n, v['result'], ', '.join(RESULTS)))
            continue
        if result != word:
            mapped['%s->%s' % (word, result)] += 1
        source, problem = reg.resolve(v['source'], args.new_source)
        if problem:
            problems.append('line %d: source %s' % (n, problem))
            continue
        if not reg.lookup(v['source']):
            unregistered[source] += 1
        if not normalize(v['query']):
            problems.append('line %d: empty query' % n)
            continue
        try:
            date, next_date = iso_date(v.get('date', '')), iso_date(v.get('next_date', ''))
        except ValueError:
            problems.append('line %d: a date is not YYYY-MM-DD' % n)
            continue
        control = clean(v.get('control')) or clean(args.control_ok)
        if result == 'none' and control:
            if control not in checked:
                checked[control] = control_problem(control)
            if checked[control]:
                problems.append('line %d: control %s' % (n, checked[control]))
                continue
        if proven(result, control) != result:
            result, unproven = 'unproven', unproven + 1
        row = {'person_key': key(v.get('person_key', '')), 'person_label': v.get('person_label', ''),
               'source': source, 'query': normalize(v['query']), 'date': date, 'result': result,
               'evidence': v.get('evidence', ''), 'agent': v.get('agent') or args.agent,
               'next_date': next_date, 'control': control}
        if same(row) in seen:
            skipped += 1
            continue
        seen.add(same(row))
        out.append(row)
    if problems:
        for p in problems[:20]:
            sys.stderr.write('gwt-ledger: %s\n' % p)
        return err('%d of %d rows refused, nothing imported' % (len(problems), len(lines) - 1))
    path = args.ledger
    if args.ledger_dir:
        if not key(args.agent):
            return err('import-tsv with --ledger-dir needs --agent: rows go to <ledger-dir>/<agent>.tsv')
        path = os.path.join(args.ledger_dir, key(args.agent) + '.tsv')
    if out:
        add_rows(path, out)
    print('imported\t%d' % len(out))
    print('skipped\t%d' % skipped)
    print('unproven\t%d' % unproven)
    for m, n in sorted(mapped.items()):
        print('synonym\t%s\t%d' % (m, n))
    for s, n in sorted(unregistered.items()):
        print('not-in-registry\t%s\t%d' % (s, n))
    return 0


# ---------------------------------------------------------------- image ranges of a book, as ordinary rows

# A scan row is an ordinary row whose normalized query is `<book> img <first> <last>`: readable in the TSV, matched
# by check, retracted by void. Any other query is no scan row, so no hand-written row can make `scan check` fail.
SCAN_QUERY = re.compile(r'^(.+) img (\d+) (\d+)$')


def scan_span(row, book):
    """(first, last) when the row records images first..last of this normalized book, else None."""
    m = SCAN_QUERY.match(normalize(row['query']))
    if not m or m.group(1) != book:
        return None
    first, last = int(m.group(2)), int(m.group(3))
    return (first, last) if 1 <= first <= last else None


def image_list(text):
    """`165,167,240-241` -> {165, 167, 240, 241}; anything else is an argparse error."""
    out = set()
    for part in text.split(','):
        m = re.fullmatch(r'\s*(\d+)\s*(?:-\s*(\d+)\s*)?', part)
        first, last = (int(m.group(1)), int(m.group(2) or m.group(1))) if m else (0, 0)
        if not 1 <= first <= last:
            raise argparse.ArgumentTypeError('not an image list: %r (say 165,167,240-241)' % text)
        out.update(range(first, last + 1))
    return out


def read_runs(first, last, skip):
    """The contiguous runs of first..last left once the skipped images (all inside it) are taken out."""
    runs, start = [], first
    for n in sorted(skip) + [last + 1]:
        if n > start:
            runs.append((start, n - 1))
        start = max(start, n + 1)
    return runs


def scan_gaps(start, end, intervals):
    cursor, gaps = start, []
    for lo, hi in sorted(intervals):
        lo, hi = max(lo, start), min(hi, end)
        if lo > hi:
            continue
        if lo > cursor:
            gaps.append([cursor, lo - 1])
        cursor = max(cursor, hi + 1)
    if cursor <= end:
        gaps.append([cursor, end])
    return gaps


def scan_command(args, reg, err, stray, lane_path, source, paths):
    book = normalize(args.book)
    if not book:
        return err('--book %r: nothing left to match after normalising' % args.book)
    if not 1 <= args.start <= args.end:
        return err('scan range must be positive and inclusive: 1 <= --start <= --end')
    person = key(args.person or '')
    if args.scmd == 'add':
        if args.skip and (min(args.skip) < args.start or max(args.skip) > args.end):
            return err('--skip names images outside --start..--end')
        runs = read_runs(args.start, args.end, args.skip)
        if not runs:
            return err('--skip leaves no image read')
        path = lane_path()
        if stray(path):
            return 2
        if args.control_ok:
            problem = control_problem(args.control_ok)
            if problem:
                return err('--control-ok: %s' % problem)
        result = proven(args.result, args.control_ok)
        if result != args.result:
            sys.stderr.write('gwt-ledger: stored as unproven: a none needs --control-ok, the proof the source could '
                             'answer; the images count as not read\n')
        for first, last in runs:
            log_search(path, {'person_key': person, 'person_label': args.label or ('' if person else args.book),
                              'source': source, 'query': normalize('%s img %d %d' % (book, first, last)),
                              'date': args.date, 'result': result, 'evidence': args.evidence, 'agent': args.agent,
                              'next_date': '', 'control': clean(args.control_ok)}, reg, paths)
        return 0
    rows, every = [], live([r for lp in paths for r in read_rows(lp)])
    odd_rows(every)
    for r in every:
        if reg.canon(r['source']) != source:
            continue
        if person and key(r['person_key']) not in (person, ''):
            continue  # a read logged with no person was a read of every image, for whoever is sought
        span = scan_span(r, book)
        if span and span[0] <= args.end and span[1] >= args.start:
            rows.append((r, span))
    done = [span for r, span in rows if r['result'] in SEARCHED and (not next_day(r) or next_day(r) > args.date)]
    gaps = scan_gaps(args.start, args.end, done)
    print(json.dumps({'gaps': gaps, 'rows': [dict({c: r[c] for c in ('id', 'person_key', 'date', 'result', 'agent',
                                                                       'evidence')}, start=s[0], end=s[1])
                                             for r, s in rows]}, ensure_ascii=False))
    return 1 if gaps else 0


# ---------------------------------------------------------------- command line


def main(argv=None):
    try:
        load_registry()  # before parsing, so even --help fails when the shipped registry is missing
    except (OSError, ValueError) as exc:
        sys.stderr.write('gwt-ledger: %s\n' % exc)
        return 2
    p = argparse.ArgumentParser(prog='gwt-ledger', description=__doc__.split('\n')[0])
    p.add_argument('--ledger', default=os.environ.get('GWT_LEDGER') or None)
    p.add_argument('--ledger-dir', default=os.environ.get('GWT_LEDGER_DIR', ''),
                   help='one file per lane: add writes <dir>/<agent>.tsv, readers read the ledger and every file here')
    p.add_argument('--registry', default=os.environ.get('GWT_LEDGER_SOURCES', ''),
                   help='your own sources file, same columns as ledger/sources.tsv; it extends the shipped one')
    p.add_argument('--requests', default=os.environ.get('GWT_REQUESTS', ''),
                   help='the requests file (default: requests.tsv beside the ledger)')
    sub = p.add_subparsers(dest='cmd', required=True)

    def agent_and_date(sp):
        sp.add_argument('--agent', default=os.environ.get('GWT_AGENT', ''))
        sp.add_argument('--date', default=datetime.date.today().isoformat(), type=iso_date)

    sc = sub.add_parser('scan', help='image ranges of a book: log the images read, or print the ones nobody read')
    scsub = sc.add_subparsers(dest='scmd', required=True)
    for action in ('add', 'check'):
        sp = scsub.add_parser(action)
        sp.add_argument('--source', required=True)
        sp.add_argument('--book', required=True, help='the book\'s id at the source: a film, catalogue or item id')
        sp.add_argument('--start', type=int, required=True, help='first image')
        sp.add_argument('--end', type=int, required=True, help='last image')
        sp.add_argument('--person', help='add: the person sought (none: every image was read in full); check: count '
                                         'this person\'s rows and the rows with no person (none: every row)')
        sp.add_argument('--new-source', action='store_true')
        agent_and_date(sp)
        if action == 'add':
            sp.add_argument('--label', default='', help='person_label; default, the book when there is no --person')
            sp.add_argument('--skip', type=image_list, default=set(), help='images not read: 165,167,240-241')
            sp.add_argument('--result', required=True, choices=RESULTS)
            sp.add_argument('--control-ok', default='', metavar='RUN')
            sp.add_argument('--evidence', default='')

    c = sub.add_parser('check', help='has this search been done? print the matching rows; exit 0 if one is a '
                                     'search that ran and is not due again, 1 if not')
    c.add_argument('--person'); c.add_argument('--source'); c.add_argument('--query')
    c.add_argument('--label', help=argparse.SUPPRESS)
    agent_and_date(c)  # accepted so a lane can pass check the arguments it passes add; --date is the day to judge by
    c.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD)
    c.add_argument('--exact', action='store_true',
                   help='only rows of this --source (required) with the identical normalized query; safe for automatic reuse')
    c.add_argument('--any-result', action='store_true',
                   help='exit 0 on any matching row, blocked, denied or due again included')
    c.add_argument('--new-source', action='store_true', help='allow a source the registry does not know that is close to a known name')
    c.add_argument('--jsonl', metavar='FILE',
                   help='many checks against one read of the ledger: one JSON object per line ("person", "source", '
                        '"query", optional "exact", "any_result", "threshold"; - reads stdin); prints one JSON answer '
                        'per line, exit 0')
    a = sub.add_parser('add', help='append a row (safe with concurrent writers)')
    a.add_argument('--person', required=True); a.add_argument('--label', default='')
    a.add_argument('--source', required=True); a.add_argument('--query', required=True)
    a.add_argument('--result', required=True, choices=RESULTS)
    a.add_argument('--control-ok', default='', metavar='RUN',
                   help='proof the source could answer: the saved output of `gwt read <source> control`, or a run id; '
                        'a none without it is stored as unproven')
    a.add_argument('--evidence', default='')
    agent_and_date(a)
    a.add_argument('--next-date', default='', type=iso_date, help='ISO date to look again, e.g. the end of an appeal window')
    a.add_argument('--new-source', action='store_true', help='log a source close to a known name as its own slug')
    v = sub.add_parser('void', help='retract every row of one exact search (same person, source and normalized '
                                    'query): a row logged by mistake; a later row of that search counts again')
    v.add_argument('--person', required=True); v.add_argument('--source', required=True)
    v.add_argument('--query', required=True); v.add_argument('--reason', required=True)
    v.add_argument('--label', default='', help='the void row\'s person_label; default, the first retracted row\'s')
    agent_and_date(v)
    v.add_argument('--new-source', action='store_true', help=argparse.SUPPRESS)
    d = sub.add_parser('due', help='rows whose next_date is on or before a day (default today)')
    d.add_argument('--on', default='', type=iso_date)
    d.add_argument('--ics', action='store_true', help='print a calendar (.ics) of every pending next_date, ledger rows '
                                                      'and open requests; with --on, only those up to that day')
    l = sub.add_parser('list', help='rows for one person')
    l.add_argument('--person', required=True); l.add_argument('--source')
    st = sub.add_parser('stats', help='counts by result, source and agent')
    st.add_argument('--by', choices=('source',), help='hit, partial and blocked rates per canonical source')
    so = sub.add_parser('sources', help='the source registry')
    so.add_argument('--country')
    cv = sub.add_parser('coverage', help='person x source matrix: hit, none, blocked, due, n/a (outside the years), -')
    cv.add_argument('--dump', required=True, help='a gwt-check dump or a gwt-apply snapshot')
    cv.add_argument('--sources', required=True, help='file of source ids, one per line (the first column)')
    cv.add_argument('--root', help='Gramps id: this person and their ancestors only')
    im = sub.add_parser('import-tsv', help='append the rows of another TSV log, mapped column by column')
    im.add_argument('file')
    im.add_argument('--map', required=True, help='LEDGER_COLUMN=FILE_COLUMN,... e.g. person_key=who,source=where,'
                                                 'query=what,result=outcome')
    im.add_argument('--agent', default=os.environ.get('GWT_AGENT', ''), help='for rows with no agent column')
    im.add_argument('--new-source', action='store_true', help='keep sources close to a known name as their own slugs')
    im.add_argument('--control-ok', default='', metavar='RUN',
                    help='the control run that vouches for every none of the file with no control of its own; '
                         'without it those rows are stored as unproven')
    rq = sub.add_parser('request', help='letters, emails and forms sent to archives, with due dates and costs')
    rsub = rq.add_subparsers(dest='rcmd', required=True)
    for name in ('add', 'update'):
        r = rsub.add_parser(name)
        if name == 'update':
            r.add_argument('id')
        else:
            r.add_argument('--person', required=True); r.add_argument('--label', default='')
            r.add_argument('--archive', required=True)
        r.add_argument('--channel', help='email, post, sede, form, phone, in-person...', required=name == 'add')
        r.add_argument('--thread', default='', help='where the conversation lives: a mail thread or a case number')
        r.add_argument('--sent', default='', type=iso_date)
        r.add_argument('--next-date', default='', type=iso_date, help='when to chase or expect an answer')
        r.add_argument('--cost', type=amount); r.add_argument('--currency', default='')
        r.add_argument('--payer', default='')
        r.add_argument('--status', choices=STATUSES, default='sent' if name == 'add' else None)
        r.add_argument('--note', default='')
    r = rsub.add_parser('list'); r.add_argument('--person'); r.add_argument('--status', choices=STATUSES)
    r = rsub.add_parser('due'); r.add_argument('--on', default='', type=iso_date)
    co = sub.add_parser('costs', help='request costs totalled per payer (or archive, person) and currency')
    co.add_argument('--by', choices=('payer', 'archive', 'person'), default='payer')
    args = p.parse_args(argv)

    def err(msg):
        sys.stderr.write('gwt-ledger: %s\n' % msg)
        return 2

    # Nobody named a ledger: reads fall back to ./buscado.tsv, but a write must not create it. A logger run
    # without its variables put a lane's rows in a stray ./buscado.tsv, and the rerun was refused as logged.
    unnamed = args.ledger is None
    if unnamed:
        args.ledger = 'buscado.tsv'

    def stray(path):
        if unnamed and path == args.ledger and not os.path.exists(path):
            return err('no ledger named and %s does not exist: pass --ledger or set $GWT_LEDGER (and --ledger-dir '
                       'or $GWT_LEDGER_DIR for lane files); nothing written' % os.path.abspath(path))

    # A query or person that normalizes to nothing (only punctuation, say) would match every row.
    for name in ('query', 'person', 'reason'):
        value = getattr(args, name, None)
        if args.cmd == 'void' and name == 'person' and value == '':
            continue  # void --person '': the rows logged with no person (a whole book or collection searched)
        if value is not None and not normalize(value):
            p.error('--%s %r: nothing left to match after normalising' % (name, value))
    try:
        reg = load_registry(args.registry)
    except (OSError, ValueError) as exc:
        return err('cannot read the source registry: %s' % exc)

    def source_id(value, note=False):
        sid, problem = reg.resolve(value, args.new_source)
        if problem:
            p.error('--source ' + problem)
        if note and not reg.lookup(value):
            sys.stderr.write('gwt-ledger: note: %r is not in the registry; logged as %s\n' % (value, sid))
        return sid

    requests_path = args.requests or os.path.join(os.path.dirname(os.path.abspath(args.ledger)), 'requests.tsv')
    today = datetime.date.today().isoformat()

    if args.cmd == 'sources':
        sys.stdout.write('\t'.join(REGISTRY_COLUMNS) + '\n')
        for row in reg.sources.values():
            if not args.country or row['country'].upper() == args.country.upper():
                sys.stdout.write('\t'.join(row[c] for c in REGISTRY_COLUMNS) + '\n')
        return 0
    def lane_path():
        if not args.ledger_dir:
            return args.ledger
        if not key(args.agent):
            p.error('%s needs --agent (or $GWT_AGENT): rows go to <ledger-dir>/<agent>.tsv' % args.cmd)
        return os.path.join(args.ledger_dir, key(args.agent) + '.tsv')

    if args.cmd == 'scan':
        try:
            return scan_command(args, reg, err, stray, lane_path, source_id(args.source, note=args.scmd == 'add'),
                                ledger_paths(args.ledger, args.ledger_dir))
        except (OSError, UnicodeDecodeError) as exc:
            return err('cannot read the ledger: %s' % exc)
    if args.cmd == 'add':
        path = lane_path()
        if stray(path):
            return 2
        source = source_id(args.source, note=True)
        if args.control_ok:
            problem = control_problem(args.control_ok)
            if problem:
                return err('--control-ok: %s' % problem)
        result = proven(args.result, args.control_ok)
        if result != args.result:
            sys.stderr.write('gwt-ledger: stored as unproven: a none needs --control-ok, the proof the source could '
                             'answer; the search counts as not done\n')
        row = {'person_key': key(args.person), 'person_label': args.label,
               'source': source, 'query': normalize(args.query), 'date': args.date,
               'result': result, 'evidence': args.evidence, 'agent': args.agent,
               'next_date': args.next_date, 'control': clean(args.control_ok)}
        try:
            log_search(path, row, reg, ledger_paths(args.ledger, args.ledger_dir))
        except (OSError, UnicodeDecodeError) as exc:
            return err('cannot read the ledger: %s' % exc)
        return 0
    if args.cmd == 'import-tsv':
        if not args.ledger_dir and stray(args.ledger):
            return 2
        return import_tsv(args, reg, err)
    if args.cmd in ('request', 'costs'):
        try:
            reqs = fold_requests(read_rows(requests_path, REQUEST_COLUMNS))
        except (OSError, UnicodeDecodeError) as exc:
            return err('cannot read the requests file: %s' % exc)
        if args.cmd == 'costs':
            totals = collections.defaultdict(lambda: [decimal.Decimal(0), 0])
            col = {'person': 'person_key'}.get(args.by, args.by)
            for r in reqs:
                if r['cost']:
                    try:
                        cost = decimal.Decimal(r['cost'])
                    except decimal.InvalidOperation:  # a hand edit such as 12,50: say so, never guess
                        sys.stderr.write('gwt-ledger: %s: cost %r is not a number, left out\n' % (r['id'], r['cost']))
                        continue
                    t = totals[(r[col] or '-', r['currency'].upper() or '-')]
                    t[0] += cost
                    t[1] += 1
            for (who, cur), (total, n) in sorted(totals.items()):
                print('%s\t%s\t%s\t%d' % (who, cur, total, n))  # never summed across currencies
            return 0
        if args.rcmd in ('add', 'update'):
            row = {c: getattr(args, c, '') or '' for c in REQUEST_COLUMNS if c not in ('person_key', 'person_label')}
            row.update(next_date=args.next_date, currency=args.currency.upper(), date=today)
            if args.rcmd == 'add':
                row.update(id='', person_key=key(args.person), person_label=args.label, archive=args.archive)
            rid = add_request(requests_path, row)
            if rid is None:
                return err('no request %s in %s' % (args.id, requests_path))
            print(rid)
            return 0
        if args.rcmd == 'list':
            sel = [r for r in reqs if (not args.person or key(r['person_key']) == key(args.person))
                   and (not args.status or r['status'] == args.status)]
        else:
            on = args.on or today
            sel = sorted((r for r in reqs if r['status'] not in CLOSED and r['next_date'] and r['next_date'] <= on),
                         key=lambda r: r['next_date'])
        print_rows([(1.0, r) for r in sel], columns=REQUEST_COLUMNS)
        return 0
    batch = None
    if args.cmd == 'check' and args.jsonl:
        if args.person is not None or args.source is not None or args.query is not None:
            p.error('check --jsonl takes person, source and query from its lines, not from --person, --source or --query')
        try:
            if args.jsonl == '-':
                lines = sys.stdin.readlines()  # stdin is the caller's: read it, never close it
            else:
                with open(args.jsonl, encoding='utf-8') as stream:
                    lines = stream.readlines()
            batch = read_checks(lines)
        except (OSError, UnicodeDecodeError) as e:
            p.error('cannot read %s: %s' % (args.jsonl, e))
        except ValueError as e:
            return err(str(e))
    try:
        everything = [r for path in ledger_paths(args.ledger, args.ledger_dir) for r in read_rows(path)]
    except (OSError, UnicodeDecodeError) as exc:  # an unreadable ledger is an error, never "not searched yet"
        return err('cannot read the ledger: %s' % exc)
    rows = live(everything)  # list alone shows the void rows and what they retracted
    odd_rows(rows)  # one note per command, never one per row
    if args.cmd == 'void':
        path = lane_path()
        source = source_id(args.source)
        targets = [r for _, r in find(rows, args.person, source, args.query, exact=True, registry=reg)
                   if key(r['person_key']) == key(args.person)]  # find drops the person filter for ''
        if not targets:
            done = find([r for r in everything if r['result'] == VOID], args.person, source, args.query, exact=True,
                        registry=reg)
            return err('no row to void: nothing live for %s, %s, %r%s' % (
                key(args.person), source, normalize(args.query),
                '; already voided by %s' % ' '.join(r['id'] for _, r in done) if done else ''))
        print_rows([(1.0, r) for r in targets])
        label = args.label or targets[0]['person_label']  # an empty middle cell shifts tab-split readers
        add_row(path, {'person_key': key(args.person), 'person_label': label, 'source': source,
                       'query': normalize(args.query), 'date': args.date, 'result': VOID,
                       'evidence': 'voids %s; %s' % (' '.join(r['id'] for r in targets), args.reason),
                       'agent': args.agent, 'next_date': '', 'control': ''})
        sys.stderr.write('gwt-ledger: voided %d row(s)\n' % len(targets))
        return 0
    if args.cmd == 'check' and batch is not None:
        by_person = collections.defaultdict(list)
        for r in rows:
            by_person[key(r['person_key'])].append(r)
        answers = []
        for n, obj in batch:
            def opt(name, default):
                return default if obj.get(name) is None else obj[name]
            problem, scored, done = check_search(
                rows, reg, obj.get('person'), obj.get('source'), obj.get('query'), opt('threshold', args.threshold),
                opt('exact', args.exact), opt('any_result', args.any_result), args.new_source, args.date, by_person)
            if problem:  # nothing printed yet: a bad line never leaves half a batch of answers behind
                return err('line %d: %s' % (n, problem))
            answers.append(dict(obj, done=done, rows=[{c: r[c] for c in ('id', 'date', 'result', 'agent', 'next_date')}
                                                      for _, r in scored]))
        for a in answers:
            print(json.dumps(a, ensure_ascii=False))
        return 0
    if args.cmd == 'check':
        problem, scored, done = check_search(rows, reg, args.person, args.source, args.query, args.threshold,
                                             args.exact, args.any_result, args.new_source, args.date)
        if problem:
            p.error(problem)
        print_rows(scored, with_score=True)
        return 0 if done else 1
    if args.cmd == 'due':
        if args.ics:
            try:
                reqs = fold_requests(read_rows(requests_path, REQUEST_COLUMNS))
            except (OSError, UnicodeDecodeError) as exc:
                return err('cannot read the requests file: %s' % exc)
            events = []
            for r in rows:
                if next_day(r) and (not args.on or next_day(r) <= args.on):
                    uid = hashlib.sha1('\t'.join(r[c] for c in COLUMNS).encode('utf-8')).hexdigest()[:16]
                    events.append((uid, r['next_date'], 'Look again: %s, %s' % (r['person_label'] or r['person_key'],
                                                                               r['source']),
                                   'query: %s; last result: %s; evidence: %s' % (r['query'], r['result'],
                                                                                 r['evidence'] or '-')))
            for r in reqs:
                if r['next_date'] and r['status'] not in CLOSED and (not args.on or r['next_date'] <= args.on):
                    events.append((r['id'], r['next_date'], 'Request %s to %s (%s)' % (r['id'], r['archive'], r['status']),
                                   'person: %s; channel: %s; thread: %s; sent: %s; cost: %s %s; payer: %s; %s' % (
                                       r['person_label'] or r['person_key'], r['channel'], r['thread'] or '-',
                                       r['sent'] or '-', r['cost'] or '-', r['currency'], r['payer'] or '-', r['note'])))
            good = []
            for e in events:
                try:
                    datetime.date.fromisoformat(e[1])
                    good.append(e)
                except ValueError:  # a hand-edited date; `due` still lists the row
                    sys.stderr.write('gwt-ledger: next_date %r is not YYYY-MM-DD, left out of the calendar\n' % e[1])
            sys.stdout.write(ics(sorted(good, key=lambda e: e[1])))
            return 0
        on = args.on or today
        due = [(1.0, r) for r in rows if next_day(r) and next_day(r) <= on]
        due.sort(key=lambda x: x[1]['next_date'])
        print_rows(due)
        return 0
    if args.cmd == 'list':
        gone = retracted(everything)  # two more cells: the row's id, and `voided` on a row a void names
        for _, r in find(everything, args.person, args.source, registry=reg):
            sys.stdout.write('\t'.join([r[c] for c in COLUMNS] + [r['id'], 'voided' if r['id'] in gone else '']) + '\n')
        return 0
    if args.cmd == 'coverage':
        return coverage(args, reg, rows, err)
    if args.by == 'source':
        print('source\trows\thit\tpartial\tnone\tblocked\thit_rate\tpartial_rate\tblocked_rate')
        counts = collections.defaultdict(collections.Counter)
        for r in rows:
            counts[reg.canon(r['source'])][r['result']] += 1
        for src, n in sorted(counts.items(), key=lambda x: (-sum(x[1].values()), x[0])):
            total = sum(n.values())
            print('%s\t%d\t%d\t%d\t%d\t%d\t%.2f\t%.2f\t%.2f' % (
                src, total, n['hit'], n['partial'], n['none'], n['blocked'],
                n['hit'] / total, n['partial'] / total, n['blocked'] / total))
        return 0
    print('rows\t%d' % len(rows))
    print('persons\t%d' % len({key(r['person_key']) for r in rows}))
    for col in ('result', 'source', 'agent'):
        for value, n in collections.Counter(r[col] for r in rows).most_common():
            print('%s\t%s\t%d' % (col, value or '-', n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
