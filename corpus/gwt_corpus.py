#!/usr/bin/env python3
"""gwt-corpus: search everything already downloaded before going back to a site.

    gwt-corpus update ROOT... [--db FILE]
    gwt-corpus search TEXT [--source S] [--limit N] [--near N | --phrase] [--fts EXPR --control EXPR] [--db FILE]
    gwt-corpus sweep (--names persons.tsv | --dump DUMP) --control NAME [--control NAME...] [--max-pages N]
                     [--ledger FILE --agent A] [--db FILE]
    gwt-corpus xref TEXT_OR_PDF --dump DUMP [--max-pair-pages N] [--max-surname-people N] [--max-place-pages N]
                    [--format json|tsv]

update walks each ROOT and indexes, in one SQLite FTS5 file, every gwt-fscache answer (`<key>.body` with its
`<key>.json`), every reader envelope (`<key>.json` with url and text or json), and every .txt, .md, .html and .json
file, plus every PDF one page per row when `pdftotext` (poppler) is on PATH. A .txt holding form feeds is split into
pages. Each row keeps the source (fscache, reader or file), the URL (from the fscache entry, the envelope, or the
folder's manifest.tsv), the file's sha256 and the page. Only files whose size, time and sha256 changed are read
again; files gone from a ROOT leave the index. A PDF that could not be read yet and a file that fails are not
recorded, so the next update tries them again. One update at a time (a lock file beside the database).

search folds accents and case (tokenizer unicode61 remove_diacritics 2). The words must all appear within --near
words of each other (default 10), in any order: never OR. A `+` or an `OR` in the text is a separator or a word,
never an operator. --phrase asks for the words side by side. 0 hits prints «not downloaded yet»: the corpus has no
copy, which never means the record does not exist. --fts passes a raw FTS5 expression and needs --control, a raw
expression known to match: when the control finds nothing the search is void (exit 3).

sweep looks for every person of a TSV (key, given, surname[, label]) or of a `gwt-check dump`: the full name as a
phrase, and the last two surnames as a phrase when there are two (particles such as de, la, van are never a surname).
A two-word phrase on more than --max-pages pages is too common to be a lead. Each --control is a full name known to
be in the corpus, searched before and after the people: when one finds nothing at either end the run is void (exit 3)
and no ledger row is written. With --ledger, one gwt-ledger row per person and phrase: source corpus, `partial` for a
match (a lead), `none` due again at once for a miss.

xref reads a book or gazette (text with one page per form feed, or a PDF through pdftotext) against a dump. A
dictionary heading «SURNAME SURNAME, Given» is also read as «Given Surname Surname». Three kinds of match: name (the
full name on the page), pair (the two surnames side by side, only when that pair is on --max-pair-pages pages or
fewer; a two-word name too), place (a surname and the person's birth or death town on one page, only for surnames
that --max-surname-people people of the tree or fewer carry, and on --max-place-pages pages or fewer). Output: gwt-rank candidates whose id is `md5:<md5 of the file>#p<page>`. Each one
is a lead to read, never a finding.

Exit: 0 hits, 1 none, 2 bad input, 3 void (a control found nothing). Standard library only.
"""
import argparse, collections, fcntl, gzip, hashlib, html, json, os, re, shutil, sqlite3, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from gwt_core import rules
except ModuleNotFoundError:  # a clone: the shared core sits next to this folder
    sys.path.insert(0, os.path.join(HERE, '..', 'core'))
    from gwt_core import rules

TOKENIZE = 'unicode61 remove_diacritics 2'  # «Olmedo», «olmédo» and «OLMEDO» are one word
NEAR = 10
SOURCES = ('fscache', 'reader', 'file')
TEXT_EXT = {'.txt', '.md', '.html', '.htm', '.json', '.pdf'}
SKIP_FILES = {'manifest.tsv', 'rawstore.tsv'}
# JSON keys whose values are ids, links or types, never words a person would search for, and the request a search
# answer repeats back (query, q, params, responseHeader): a 0-result answer must not match the name it was asked for
SKIP_KEYS = {'id', 'type', 'labelId', 'lang', 'links', 'href', 'resource', 'resourceId', 'qualifiers', 'confidence',
             'principal', 'handle', 'captured_at', 'url', 'final_url', 'query', 'q', 'params', 'responseHeader'}
WORD = re.compile(r'[^\W_]+')
# surname particles of every locale in gwt_core.rules (de, la, van, von, di...): never a surname word on their own
PARTICLES = frozenset().union(*(rules.load_locale(c)['_particles'] for c in rules.LOCALES))
JUNK = re.compile(r'^(https?:|#|data:)|^[0-9a-f]{8}-[0-9a-f]{4}-|^[\d\s.:+-]*$')
SETTLEMENT = {'City', 'Town', 'Village', 'Hamlet', 'Municipality', 'Locality', 'Parish', 'Borough', 'Neighborhood'}
EXIT_HITS, EXIT_NONE, EXIT_BAD, EXIT_VOID = 0, 1, 2, 3


class BadInput(Exception):
    pass


class PdfUnreadable(Exception):
    pass


# ---------------------------------------------------------------- text out of each kind of file

def json_strings(o, out, key=None):
    """Every human string in a JSON value: names, dates, places, transcriptions; no ids, links or type URIs."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k not in SKIP_KEYS:
                json_strings(v, out, k)
    elif isinstance(o, list):
        for v in o:
            json_strings(v, out, key)
    elif isinstance(o, str):
        s = o.strip()
        if s and not JUNK.search(s):
            out.append(s)
    return out


def strip_html(s):
    s = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', s)
    return html.unescape(re.sub(r'<[^>]+>', ' ', s))


def split_pages(text):
    """One string per form feed, without the empty page after a final form feed."""
    pages = text.split('\f')
    return pages[:-1] if len(pages) > 1 and not pages[-1].strip() else pages


def pdf_pages(path):
    """The text of each page of a PDF, or None when pdftotext is not on PATH. PdfUnreadable when it fails or
    times out, so the caller never takes a failed read for a PDF with no words."""
    if not shutil.which('pdftotext'):
        return None
    try:
        r = subprocess.run(['pdftotext', '-q', '-enc', 'UTF-8', path, '-'], capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PdfUnreadable(str(e))
    if r.returncode:
        raise PdfUnreadable('pdftotext exit %d' % r.returncode)
    return split_pages(r.stdout.decode('utf-8', 'replace'))


def read_text(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8-sig', 'replace').replace('\r\n', '\n')


def is_dump(o):
    """A gwt-check dump or a tree export: the tree itself, never a source to find its people in."""
    objs = o.get('objects') if isinstance(o.get('objects'), dict) else o
    return isinstance(objs.get('people', objs.get('Person')), list)


def entry_url(entry, fallback):
    """The record a GEDCOM X search entry points at (its links or identifiers), else the search URL."""
    links = entry.get('links') if isinstance(entry.get('links'), dict) else {}
    for k in ('record', 'persona', 'person', 'self'):
        href = (links.get(k) or {}).get('href') if isinstance(links.get(k), dict) else None
        if isinstance(href, str) and href.startswith('http'):
            return href
    ids = entry.get('identifiers') if isinstance(entry.get('identifiers'), dict) else {}
    for v in ids.values():
        for u in v if isinstance(v, list) else [v]:
            if isinstance(u, str) and u.startswith('http'):
                return u
    return fallback


def body_text(raw):
    """Words from an fscache body: JSON strings, HTML text or plain text; '' for an image or other binary."""
    head = raw.lstrip()[:1]
    if head in (b'{', b'['):
        try:
            return ' · '.join(json_strings(json.loads(raw), []))
        except ValueError:
            pass
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return ''
    return strip_html(text) if head == b'<' else text


def folder_urls(folder, cache):
    """file name -> URL from a gwt-fetch or reader manifest.tsv in this folder."""
    if folder not in cache:
        urls = {}
        try:
            lines = read_text(os.path.join(folder, 'manifest.tsv')).splitlines()
        except OSError:
            lines = []
        cols = lines[0].split('\t') if lines else []
        for line in lines[1:]:
            row = dict(zip(cols, line.split('\t')))
            url = row.get('image_url') or row.get('url') or row.get('manifest_url') or ''
            if row.get('file') and url:
                urls[row['file']] = url
        cache[folder] = urls
    return cache[folder]


def extract(path, ctx):
    """[(source, title, body, url, page, date)] for one file, None when it is an fscache body whose metadata is not
    written yet (the next update retries it), [] when there is nothing to index."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if name.endswith('.body'):
        try:
            with open(path[:-5] + '.json', encoding='utf-8') as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return None  # its metadata is not written yet
        with open(path, 'rb') as f:
            raw = f.read()
        url, date = meta.get('url', ''), (meta.get('date') or '')[:10]
        try:
            o = json.loads(raw) if raw.lstrip()[:1] == b'{' else None
        except ValueError:
            o = None
        if isinstance(o, dict) and isinstance(o.get('entries'), list) and len(o['entries']) > 1:
            # a search answer: one row per record, each with its own link, so a hit leads to the record
            return [('fscache', url, ' · '.join(json_strings(e, [])), entry_url(e, url) if isinstance(e, dict) else url,
                     0, date) for e in o['entries']]
        return [('fscache', url, body_text(raw), url, 0, date)]
    if ext == '.pdf':
        try:
            pages = pdf_pages(path)
        except PdfUnreadable:
            ctx['pdf_failed'] += 1
            return None  # not recorded: the next update tries it again
        if pages is None:
            ctx['no_pdftotext'] += 1
            return None  # not recorded: read once pdftotext is installed
        if not any(p.strip() for p in pages):
            ctx['pdf_no_text'] += 1
        url = folder_urls(os.path.dirname(path), ctx['urls']).get(name, '')
        return [('file', name, p, url, i + 1, '') for i, p in enumerate(pages)]
    text = read_text(path)
    url = folder_urls(os.path.dirname(path), ctx['urls']).get(name, '')
    if ext == '.json':
        if os.path.exists(path[:-5] + '.body') or name.endswith('.candidates.json'):
            return []  # an fscache entry's metadata (read with its body), or a reader's normalized copy
        try:
            o = json.loads(text)
        except ValueError:
            return []
        if isinstance(o, dict) and is_dump(o):
            return []  # the tree itself: every person would find themselves
        if isinstance(o, dict) and o.get('url') and ('text' in o or 'json' in o):
            words = [o['text']] if isinstance(o.get('text'), str) else []
            words += json_strings(o.get('json'), [])
            return [('reader', o['url'], '\n'.join(words), o.get('final_url') or o['url'], 0,
                     (o.get('captured_at') or '')[:10])]
        return [('file', name, ' · '.join(json_strings(o, [])), url, 0, '')]
    if ext in ('.html', '.htm'):
        return [('file', name, strip_html(text), url, 0, '')]
    pages = split_pages(text)
    if len(pages) == 1:
        return [('file', name, text, url, 0, '')]
    return [('file', name, p, url, i + 1, '') for i, p in enumerate(pages)]


# ---------------------------------------------------------------- the index

def default_db():
    return os.environ.get('GWT_CORPUS_DB') or os.path.expanduser('~/.cache/gwt-corpus/corpus.db')


def connect(path, readonly=False):
    if readonly:
        if not os.path.exists(path):
            raise BadInput('no index at %s yet; run gwt-corpus update ROOT...' % path)
        return sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=30)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path, timeout=60)
    db.execute('PRAGMA journal_mode=WAL')
    try:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, sha TEXT);
            CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY, file TEXT, source TEXT, url TEXT, sha TEXT,
                                            page INTEGER, date TEXT);
            CREATE INDEX IF NOT EXISTS meta_file ON meta(file);
            CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(title, body, tokenize="%s");
        ''' % TOKENIZE)
    except sqlite3.OperationalError as e:
        raise BadInput('this Python\'s SQLite has no FTS5 (%s)' % e)
    return db


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def walk(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith('.') and x != 'node_modules')
        for f in sorted(files):
            if f not in SKIP_FILES and not f.endswith('.part') and \
                    (f.endswith('.body') or os.path.splitext(f)[1].lower() in TEXT_EXT):
                yield os.path.join(d, f)


def drop(db, path):
    db.execute('DELETE FROM docs WHERE rowid IN (SELECT id FROM meta WHERE file=?)', (path,))
    db.execute('DELETE FROM meta WHERE file=?', (path,))


def update(db, roots, log=print):
    """Index new and changed files under roots, drop files gone from them. Returns the counts."""
    roots = [os.path.abspath(r) for r in roots]
    ctx = {'urls': {}, 'no_pdftotext': 0, 'pdf_failed': 0, 'pdf_no_text': 0}
    known = {p: (m, s, h) for p, m, s, h in db.execute('SELECT path, mtime, size, sha FROM files')}
    seen, n = set(), collections.Counter()
    for root in roots:
        for path in walk(root):
            seen.add(path)
            try:
                st = os.stat(path)
                old = known.get(path)
                if old and old[0] == st.st_mtime and old[1] == st.st_size:
                    continue
                sha = sha256(path)
                if old and old[2] == sha:
                    db.execute('UPDATE files SET mtime=?, size=? WHERE path=?', (st.st_mtime, st.st_size, path))
                    continue
                rows = extract(path, ctx)
            except (OSError, ValueError, AttributeError, TypeError) as e:
                n['unreadable'] += 1  # one bad file never costs the run; it is tried again next time
                log('unreadable: %s (%s)' % (path, e))
                continue
            if rows is None:
                continue
            drop(db, path)
            for source, title, body, url, page, date in rows:
                if body.strip():
                    cur = db.execute('INSERT INTO meta(file, source, url, sha, page, date) VALUES (?,?,?,?,?,?)',
                                     (path, source, url, sha, page, date))
                    db.execute('INSERT INTO docs(rowid, title, body) VALUES (?,?,?)', (cur.lastrowid, title, body))
                    n['rows'] += 1
            db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?)', (path, st.st_mtime, st.st_size, sha))
            n['changed' if old else 'new'] += 1
            if (n['new'] + n['changed']) % 1000 == 0:
                db.commit()  # a long first index keeps what it has read if it stops
    for path in known:
        if path not in seen and any(path.startswith(r + os.sep) for r in roots):
            drop(db, path)
            db.execute('DELETE FROM files WHERE path=?', (path,))
            n['gone'] += 1
    db.commit()
    for k in ('no_pdftotext', 'pdf_failed', 'pdf_no_text'):
        n[k] = ctx[k]
    return n


# ---------------------------------------------------------------- search

def words(text):
    """The words of a query. Every character that is not a letter or digit separates words, so `+`, quotes and
    parentheses never reach FTS5 as operators. FTS5 folds them with the index's own rules (ß stays ß)."""
    return WORD.findall(text or '')


def fts_query(text, near=NEAR, phrase=False):
    """An FTS5 expression on the page text (never the request URL) that needs every word: one quoted phrase, or
    NEAR of the quoted words. Never OR."""
    ws = words(text)
    if not ws:
        raise BadInput('nothing to search for in %r' % text)
    if phrase or len(ws) == 1:
        return 'body : ("%s")' % ' '.join(ws)
    return 'body : (NEAR(%s, %d))' % (' '.join('"%s"' % w for w in ws), near)


def run_query(db, expr, source=None, limit=20):
    """[(source, date, url, file, page, sha, snippet)], best first."""
    sql = ('SELECT meta.source, meta.date, meta.url, meta.file, meta.page, meta.sha, '
           'snippet(docs, 1, "»", "«", " … ", 16) FROM docs JOIN meta ON meta.id = docs.rowid WHERE docs MATCH ?')
    args = [expr]
    if source:
        sql += ' AND meta.source = ?'
        args.append(source)
    sql += ' ORDER BY rank LIMIT ?'
    args.append(limit)
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        raise BadInput('bad query %r: %s' % (expr, e))


def show(r):
    source, date, url, path, page, sha, snip = r
    where = url or path
    return '[%s] %s  %s%s  sha256:%s\n    %s' % (source, date or '-', where, ' p.%d' % page if page else '',
                                                sha[:16], ' '.join(snip.split()))


def cmd_search(a):
    db = connect(a.db, readonly=True)
    if a.fts and not a.control:
        raise BadInput('--fts needs --control: a raw expression you know matches, so a 0 can be trusted')
    if a.control:
        expr = a.control if a.fts else fts_query(a.control, a.near, a.phrase)
        if not run_query(db, expr, a.source, 1):
            print('VOID: the control %s found nothing, so no answer from this corpus can be trusted' % expr)
            return EXIT_VOID
    expr = a.text if a.fts else fts_query(a.text, a.near, a.phrase)
    hits = run_query(db, expr, a.source, a.limit)
    for r in hits:
        print(show(r))
    if not hits:
        print('0 hits for %s: not downloaded yet. The corpus has no copy; that never means the record does not '
              'exist' % expr)
        return EXIT_NONE
    print('%s%d hits for %s' % ('first ' if len(hits) == a.limit else '', len(hits), expr))
    return EXIT_HITS


# ---------------------------------------------------------------- the tree's people

def text_of(v):
    return ((v.get('string') or v.get('value')) if isinstance(v, dict) else v) or ''


def load_dump(path):
    try:
        with open(path, 'rb') as f:
            gz = f.read(2) == b'\x1f\x8b'
        with (gzip.open if gz else open)(path, 'rt', encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError, EOFError) as e:
        raise BadInput('--dump %s: %s' % (path, e))
    objs = (d.get('objects') or d) if isinstance(d, dict) else None
    if not isinstance(objs, dict) or not isinstance(objs.get('people', objs.get('Person')), list):
        raise BadInput('--dump %s: not a gwt-check dump (no objects.people)' % path)
    return objs


def dump_people(objs):
    """[{key, given, surname, label, towns}] from a dump: the primary name and the birth and death towns."""
    events = {e.get('handle'): e for e in objs.get('events') or objs.get('Event') or []}
    places = {p.get('handle'): p for p in objs.get('places') or objs.get('Place') or []}

    def town(h, seen=()):
        p = places.get(h)
        if not p or h in seen:
            return ''
        if text_of(p.get('place_type')) in SETTLEMENT:
            return text_of(p.get('name')) or text_of(p.get('title'))
        refs = p.get('placeref_list') or []
        return town(refs[0].get('ref'), seen + (h,)) if refs else ''

    out = []
    for p in objs.get('people') or objs.get('Person') or []:
        n = p.get('primary_name') or {}
        given = n.get('first_name') or ''
        surname = ' '.join(s.get('surname') or '' for s in n.get('surname_list') or []).strip()
        towns = set()
        refs = p.get('event_ref_list') or []
        for i in (p.get('birth_ref_index', -1), p.get('death_ref_index', -1)):
            if isinstance(i, int) and 0 <= i < len(refs):
                t = town((events.get(refs[i].get('ref')) or {}).get('place'))
                if t:
                    towns.add(t)
        out.append({'key': p.get('gramps_id') or p.get('handle') or '', 'given': given, 'surname': surname,
                    'label': ('%s %s' % (given, surname)).strip(), 'towns': sorted(towns)})
    return out


def tsv_people(path):
    """[{key, given, surname, label}] from a TSV with a header naming key, given and surname (label optional)."""
    try:
        lines = read_text(path).splitlines()
    except OSError as e:
        raise BadInput('--names %s: %s' % (path, e))
    cols = lines[0].split('\t') if lines else []
    if not {'key', 'given', 'surname'} <= set(cols):
        raise BadInput('--names %s: the header must name key, given and surname' % path)
    out = []
    for n, line in enumerate(lines[1:], 2):
        if line.strip():
            cells = line.split('\t')
            if any(cols.index(c) >= len(cells) for c in ('key', 'given', 'surname')):
                raise BadInput('--names %s line %d: fewer columns than the header' % (path, n))
            r = dict(zip(cols, cells))
            r.setdefault('label', ('%s %s' % (r['given'], r['surname'])).strip())
            r['label'] = r['label'] or ('%s %s' % (r['given'], r['surname'])).strip()
            out.append(r)
    return out


def needles(person):
    """What sweep and xref look for: ([(kind, words, short)], surname words). The full name, and the last two
    surnames side by side from the first of them («García de la Torre»). Particles (de, la, van...) are never a
    surname of their own: «de la Torre» alone has no pair. `short` marks a needle of fewer than three real words
    (a given name and one surname, or a pair): common enough to need a rarity check before it counts."""
    given, sur = words(person['given']), words(person['surname'])
    real = [i for i, w in enumerate(sur) if rules.fold(w) not in PARTICLES]
    out = []
    if given and real:
        out.append(('name', given + sur, len(given) + len(real) < 3))
    if len(real) >= 2:
        out.append(('pair', sur[real[-2]:], True))
    return out, [sur[i] for i in real]


def cmd_sweep(a):
    if not a.control:
        raise BadInput('sweep needs --control: a full name you know is in the corpus, checked before and after')
    people = tsv_people(a.names) if a.names else dump_people(load_dump(a.dump))
    db = connect(a.db, readonly=True)

    def controls(when):
        bad = [c for c in a.control if not run_query(db, fts_query(c, phrase=True), None, 1)]
        for c in bad:
            print('VOID: the control %r found nothing %s the sweep; nothing found or missed here can be trusted'
                  % (c, when))
        return not bad

    if not controls('before'):
        return EXIT_VOID
    print('\t'.join(('key', 'label', 'match', 'phrase', 'source', 'date', 'where', 'page', 'sha256', 'snippet')))
    today = time.strftime('%Y-%m-%d')
    run_id = 'corpus-sweep-%s-%d-controls-ok' % (time.strftime('%Y%m%dT%H%M%S'), len(a.control))
    ledger_rows, hits, common = [], 0, 0
    for p in people:
        for kind, ws, short in needles(p)[0]:
            phrase = rules.fold(' '.join(ws))
            found = run_query(db, fts_query(' '.join(ws), phrase=True), None, max(a.limit, a.max_pages + 1))
            if short and len(found) > a.max_pages:
                common += 1  # a given name and one surname, or a bare pair, on many pages: no lead
                continue
            for source, date, url, path, page, sha, snip in found[:a.limit]:
                print('\t'.join((p['key'], p['label'], kind, phrase, source, date or '', url or path,
                                 str(page or ''), sha, ' '.join(snip.split()))))
            hits += bool(found)
            first = found[0] if found else None
            # a match is a lead to read (partial); a miss only says the corpus has no copy yet, so it is due again
            # at once and never stops the search on the site itself
            row = {'person_key': p['key'], 'person_label': p['label'], 'source': 'corpus', 'query': '"%s"' % phrase,
                   'date': today, 'agent': a.agent, 'control': run_id}
            if first:
                row.update(result='partial', evidence='%s%s' % (first[2] or first[3],
                                                                ' p.%d' % first[4] if first[4] else ''))
            else:
                row.update(result='none', evidence='corpus:%s' % os.path.basename(a.db), next_date=today)
            ledger_rows.append(row)
    if not controls('after'):
        return EXIT_VOID
    written = 0
    if a.ledger:
        led = ledger_module()
        have = {(r['person_key'], r['source'], r['query'], r['result'], r['date']) for r in led.read_rows(a.ledger)}
        new = [r for r in ledger_rows
               if (r['person_key'], r['source'], r['query'], r['result'], r['date']) not in have]
        if new:
            led.add_rows(a.ledger, new)
        written = len(new)
    print('%d of %d phrases found%s; %s' % (
        hits, len(ledger_rows), ', %d too common to be leads (on more than %d pages)' % (common, a.max_pages)
        if common else '', '%d ledger rows written to %s' % (written, a.ledger) if a.ledger
        else 'no ledger (--ledger not given)'), file=sys.stderr)
    return EXIT_HITS if hits else EXIT_NONE


def ledger_module():
    try:
        import gwt_ledger
    except ModuleNotFoundError:  # a clone
        sys.path.insert(0, os.path.join(HERE, '..', 'ledger'))
        import gwt_ledger
    return gwt_ledger


# ---------------------------------------------------------------- xref: a book against the tree

def flip_headings(page):
    """«SURNAME SURNAME, Given» lines (a biographical dictionary's headings) also read as «Given Surname Surname»."""
    extra = []
    for line in page.splitlines():
        left, comma, right = line.strip().partition(',')
        right = re.sub(r'[\s\d.()-]+$', '', right).strip()
        if comma and left.isupper() and len(left) > 2 and right and len(right.split()) <= 4 \
                and right[0].isupper() and all(w[0].isalpha() for w in right.split()):
            extra.append('%s %s' % (right, left.title()))
    return page + ('\n' + '\n'.join(extra) if extra else '')


def book_pages(path):
    if path.lower().endswith('.pdf'):
        try:
            pages = pdf_pages(path)
        except PdfUnreadable as e:
            raise BadInput('%s: pdftotext could not read it (%s)' % (path, e))
        if pages is None:
            raise BadInput('%s is a PDF and pdftotext (poppler) is not on PATH' % path)
        if not any(p.strip() for p in pages):
            raise BadInput('%s has no text layer; OCR it first (see ocr-bench)' % path)
    else:
        try:
            pages = split_pages(read_text(path))
        except OSError as e:
            raise BadInput('%s: %s' % (path, e))
    return [' %s ' % rules.fold(flip_headings(re.sub(r'(\w)-\n\s*(\w)', r'\1\2', p))) for p in pages]


def context(page, needle, width=100):
    k = page.find(needle)
    return page[max(0, k - width):k + len(needle) + width].strip()


def xref(pages, people, max_pair_pages=6, max_surname_people=40, max_place_pages=6):
    """[(kind, person, page number, context)]: name, pair and place matches, at most one per person and page. A
    short needle (two words) counts only on max_pair_pages pages or fewer, a surname and town only on
    max_place_pages pages or fewer: anything more common is no lead."""
    index = collections.defaultdict(set)  # folded word -> pages holding it
    for i, page in enumerate(pages, 1):
        for w in set(page.split()):
            index[w].add(i)
    cache = {}

    def on(needle):
        """Pages holding the folded phrase, side by side."""
        if needle not in cache:
            ws = needle.split()
            cand = set.intersection(*(index.get(w, set()) for w in ws)) if ws else set()
            cache[needle] = sorted(i for i in cand if ' %s ' % needle in pages[i - 1])
        return cache[needle]

    found = [(p,) + needles(p) for p in people]
    carriers = collections.Counter(s for _, _, surs in found for s in {rules.fold(x) for x in surs})
    out = []
    for p, phrases, surs in found:
        done = set()

        def hit(kind, i, needle):
            if i not in done:
                done.add(i)
                out.append((kind, p, i, context(pages[i - 1], needle)))

        for kind, ws, short in phrases:
            needle = rules.fold(' '.join(ws))
            where = on(needle)
            if not short or len(where) <= max_pair_pages:
                for i in where:
                    hit(kind, i, needle)
        towns = [rules.fold(t) for t in p.get('towns') or [] if rules.fold(t)]
        for s in {rules.fold(x) for x in surs}:
            if carriers[s] > max_surname_people:
                continue
            for t in towns:
                where = sorted(set(on(s)) & set(on(t)))
                if len(where) <= max_place_pages:
                    for i in where:
                        hit('place', i, s)
    return out


def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def cmd_xref(a):
    people = dump_people(load_dump(a.dump))
    pages = book_pages(a.file)
    digest = md5(a.file)
    found = xref(pages, people, a.max_pair_pages, a.max_surname_people, a.max_place_pages)
    cands = [{'source': 'xref', 'id': 'md5:%s#p%d' % (digest, i), 'name': p['label'], 'role': 'Mention',
              'principal': False, 'given': p['given'] or None, 'surnames': [p['surname']] if p['surname'] else None,
              'events': {}, 'relatives': [], 'match': kind, 'person': p['key'], 'md5': digest, 'page': i,
              'context': ctx} for kind, p, i, ctx in found]
    if a.format == 'tsv':
        print('\t'.join(('match', 'person', 'name', 'page', 'md5', 'context')))
        for c in cands:
            print('\t'.join((c['match'], c['person'], c['name'], str(c['page']), digest, c['context'])))
    else:
        print(json.dumps({'candidates': cands}, ensure_ascii=False, indent=1))
    print('%d matches in %d pages of %s (md5 %s)' % (len(cands), len(pages), a.file, digest), file=sys.stderr)
    return EXIT_HITS if cands else EXIT_NONE


# ---------------------------------------------------------------- command line

def cmd_update(a):
    if not a.roots:
        raise BadInput('update needs at least one ROOT folder')
    for r in a.roots:
        if not os.path.isdir(r):
            raise BadInput('%s is not a folder' % r)
    db = connect(a.db)
    with open(a.db + '.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BadInput('another update of %s is running' % a.db)
        t0 = time.time()
        n = update(db, a.roots)
    print('files: %d new, %d changed, %d gone; %d rows written (%.0f s)'
          % (n['new'], n['changed'], n['gone'], n['rows'], time.time() - t0))
    if n['no_pdftotext']:
        print('%d PDFs not read: pdftotext (poppler) is not on PATH; the next update reads them' % n['no_pdftotext'])
    if n['pdf_failed']:
        print('%d PDFs pdftotext could not read; the next update tries again' % n['pdf_failed'])
    if n['pdf_no_text']:
        print('%d PDFs have no text layer (scans): OCR them to make them searchable' % n['pdf_no_text'])
    if n['unreadable']:
        print('%d files could not be read (listed above); the next update tries again' % n['unreadable'])
    for source, count in db.execute('SELECT source, count(*) FROM meta GROUP BY source ORDER BY source'):
        print('  %-8s %8d rows' % (source, count))
    return 0


def positive(v):
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError('must be 1 or more')
    return n


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-corpus', description=__doc__.split('\n\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    def with_db(sp):
        sp.add_argument('--db', default=default_db(), help='the index (default $GWT_CORPUS_DB or '
                                                           '~/.cache/gwt-corpus/corpus.db)')
        return sp

    u = with_db(sub.add_parser('update', help='index new and changed files under each ROOT'))
    u.add_argument('roots', nargs='*', metavar='ROOT')
    s = with_db(sub.add_parser('search', help='every word, near each other, accents folded; never OR'))
    s.add_argument('text', nargs='?', default='')
    s.add_argument('--source', choices=SOURCES)
    s.add_argument('--limit', type=positive, default=20)
    s.add_argument('--near', type=int, default=NEAR, help='at most this many words between two of yours')
    s.add_argument('--phrase', action='store_true', help='the words side by side, in this order')
    s.add_argument('--fts', metavar='EXPR', help='a raw FTS5 expression instead of TEXT (needs --control)')
    s.add_argument('--control', metavar='EXPR', help='a search known to match; when it finds nothing: exit 3')
    w = with_db(sub.add_parser('sweep', help='look for every person of a list or a dump'))
    g = w.add_mutually_exclusive_group(required=True)
    g.add_argument('--names', metavar='TSV', help='columns key, given, surname[, label]')
    g.add_argument('--dump', metavar='DUMP', help='a gwt-check dump')
    w.add_argument('--control', action='append', default=[], metavar='NAME',
                   help='a full name known to be in the corpus (repeatable, at least one)')
    w.add_argument('--ledger', help='append one gwt-ledger row per person and phrase')
    w.add_argument('--agent', default='corpus-sweep', help='the ledger rows\' agent')
    w.add_argument('--limit', type=positive, default=20, help='hits listed per phrase')
    w.add_argument('--max-pages', type=int, default=6, help='a two-word phrase (given name and one surname, or a '
                                                            'surname pair) on more pages is too common to be a lead')
    x = sub.add_parser('xref', help='a book or gazette against a dump, as gwt-rank candidates')
    x.add_argument('file', metavar='TEXT_OR_PDF')
    x.add_argument('--dump', required=True)
    x.add_argument('--max-pair-pages', type=int, default=6)
    x.add_argument('--max-surname-people', type=int, default=40)
    x.add_argument('--max-place-pages', type=int, default=6)
    x.add_argument('--format', choices=('json', 'tsv'), default='json')
    a = p.parse_args(argv)
    if a.cmd == 'search' and a.fts:
        a.text = a.fts
    try:
        if a.cmd == 'search' and not a.text:
            raise BadInput('search needs TEXT or --fts EXPR')
        return {'update': cmd_update, 'search': cmd_search, 'sweep': cmd_sweep, 'xref': cmd_xref}[a.cmd](a)
    except BadInput as e:
        print('gwt-corpus: %s' % e, file=sys.stderr)
        return EXIT_BAD


if __name__ == '__main__':
    sys.exit(main())
