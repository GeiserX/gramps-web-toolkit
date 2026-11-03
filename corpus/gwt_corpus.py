#!/usr/bin/env python3
"""gwt-corpus: search everything already downloaded before going back to a site.

    gwt-corpus update ROOT... [--db FILE]
    gwt-corpus search TEXT [--source S] [--limit N] [--near N | --phrase] [--fts EXPR --control EXPR] [--db FILE]
    gwt-corpus sweep (--names persons.tsv | --dump DUMP) --control NAME [--control NAME...]
                     [--ledger FILE --agent A] [--db FILE]
    gwt-corpus xref TEXT_OR_PDF --dump DUMP [--max-pair-pages N] [--max-surname-people N] [--format json|tsv]

update walks each ROOT and indexes, in one SQLite FTS5 file, every gwt-fscache answer (`<key>.body` with its
`<key>.json`), every reader envelope (`<key>.json` with url and text or json), and every .txt, .md, .html and .json
file, plus every PDF one page per row when `pdftotext` (poppler) is on PATH. A .txt holding form feeds is split into
pages. Each row keeps the source (fscache, reader or file), the URL (from the fscache entry, the envelope, or the
folder's manifest.tsv), the file's sha256 and the page. Only files whose size, time and sha256 changed are read
again; files gone from a ROOT leave the index. One update at a time (a lock file beside the database).

search folds accents and case (tokenizer unicode61 remove_diacritics 2). The words must all appear within --near
words of each other (default 10), in any order: never OR. A `+` or an `OR` in the text is a separator or a word,
never an operator. --phrase asks for the words side by side. 0 hits prints «not downloaded yet»: the corpus has no
copy, which never means the record does not exist. --fts passes a raw FTS5 expression and needs --control, a raw
expression known to match: when the control finds nothing the search is void (exit 3).

sweep looks for every person of a TSV (key, given, surname[, label]) or of a `gwt-check dump`: the full name as a
phrase, and the two surnames as a phrase when there are two. Each --control is a full name known to be in the
corpus, searched before and after the people: when one finds nothing at either end the run is void (exit 3) and no
ledger row is written. With --ledger, one gwt-ledger row per person and phrase (source corpus, hit or none).

xref reads a book or gazette (text with one page per form feed, or a PDF through pdftotext) against a dump. A
dictionary heading «SURNAME SURNAME, Given» is also read as «Given Surname Surname». Three kinds of match: name (the
full name on the page), pair (the two surnames side by side, only when that pair is on --max-pair-pages pages or
fewer), place (a surname and the person's birth or death town on one page, only for surnames that --max-surname-people
people of the tree or fewer carry). Output: gwt-rank candidates whose id is `md5:<md5 of the file>#p<page>`. Each one
is a lead to read, never a finding.

Exit: 0 hits, 1 none, 2 bad input, 3 void (a control found nothing). Standard library only.
"""
import argparse, collections, fcntl, hashlib, html, json, os, re, shutil, sqlite3, subprocess, sys, time

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
# JSON keys whose values are ids, links or types, never words a person would search for
SKIP_KEYS = {'id', 'type', 'labelId', 'lang', 'links', 'href', 'resource', 'resourceId', 'qualifiers', 'confidence',
             'principal', 'handle', 'captured_at', 'url', 'final_url'}
JUNK = re.compile(r'^(https?:|#|data:)|^[0-9a-f]{8}-[0-9a-f]{4}-|^[\d\s.:+-]*$')
SETTLEMENT = {'City', 'Town', 'Village', 'Hamlet', 'Municipality', 'Locality', 'Parish', 'Borough', 'Neighborhood'}
EXIT_HITS, EXIT_NONE, EXIT_BAD, EXIT_VOID = 0, 1, 2, 3


class BadInput(Exception):
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


def pdf_pages(path):
    """The text of each page of a PDF, or None when pdftotext is not on PATH."""
    if not shutil.which('pdftotext'):
        return None
    try:
        r = subprocess.run(['pdftotext', '-q', '-enc', 'UTF-8', path, '-'], capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return []
    pages = r.stdout.decode('utf-8', 'replace').split('\f')
    return pages[:-1] if pages and not pages[-1].strip() else pages


def read_text(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', 'replace')


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
            text = body_text(f.read())
        return [('fscache', meta.get('url', ''), text, meta.get('url', ''), 0, (meta.get('date') or '')[:10])]
    if ext == '.pdf':
        pages = pdf_pages(path)
        if pages is None:
            ctx['no_pdftotext'] += 1
            return []
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
        if isinstance(o, dict) and o.get('url') and ('text' in o or 'json' in o):
            words = [o['text']] if isinstance(o.get('text'), str) else []
            words += json_strings(o.get('json'), [])
            return [('reader', o['url'], '\n'.join(words), o.get('final_url') or o['url'], 0,
                     (o.get('captured_at') or '')[:10])]
        return [('file', name, ' · '.join(json_strings(o, [])), url, 0, '')]
    if ext in ('.html', '.htm'):
        return [('file', name, strip_html(text), url, 0, '')]
    pages = text.split('\f')
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
    ctx = {'urls': {}, 'no_pdftotext': 0}
    known = {p: (m, s, h) for p, m, s, h in db.execute('SELECT path, mtime, size, sha FROM files')}
    seen, n = set(), collections.Counter()
    for root in roots:
        for path in walk(root):
            seen.add(path)
            st = os.stat(path)
            old = known.get(path)
            if old and old[0] == st.st_mtime and old[1] == st.st_size:
                continue
            sha = sha256(path)
            if old and old[2] == sha:
                db.execute('UPDATE files SET mtime=?, size=? WHERE path=?', (st.st_mtime, st.st_size, path))
                continue
            rows = extract(path, ctx)
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
    for path in known:
        if path not in seen and any(path.startswith(r + os.sep) for r in roots):
            drop(db, path)
            db.execute('DELETE FROM files WHERE path=?', (path,))
            n['gone'] += 1
    db.commit()
    n['no_pdftotext'] = ctx['no_pdftotext']
    return n


# ---------------------------------------------------------------- search

def words(text):
    """The words of a query, folded. Every character that is not a letter or digit separates words, so `+`, quotes
    and parentheses never reach FTS5 as operators."""
    return rules.fold(text).split()


def fts_query(text, near=NEAR, phrase=False):
    """An FTS5 expression that needs every word: one quoted phrase, or NEAR of the quoted words. Never OR."""
    ws = words(text)
    if not ws:
        raise BadInput('nothing to search for in %r' % text)
    if phrase or len(ws) == 1:
        return '"%s"' % ' '.join(ws)
    return 'NEAR(%s, %d)' % (' '.join('"%s"' % w for w in ws), near)


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
    print('%d hits for %s' % (len(hits), expr))
    return EXIT_HITS


# ---------------------------------------------------------------- the tree's people

def text_of(v):
    return ((v.get('string') or v.get('value')) if isinstance(v, dict) else v) or ''


def load_dump(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise BadInput('--dump %s: %s' % (path, e))
    objs = d.get('objects') or d
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
    for line in lines[1:]:
        if line.strip():
            r = dict(zip(cols, line.split('\t')))
            r.setdefault('label', ('%s %s' % (r['given'], r['surname'])).strip())
            r['label'] = r['label'] or ('%s %s' % (r['given'], r['surname'])).strip()
            out.append(r)
    return out


def sweep_phrases(person):
    """(kind, phrase) to look for: the full name, and the two surnames side by side when there are two."""
    given, sur = words(person['given']), words(person['surname'])
    out = []
    if given and sur:
        out.append(('name', ' '.join(given + sur)))
    if len(sur) >= 2:
        out.append(('pair', ' '.join(sur[-2:])))
    return out


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
    ledger_rows, hits = [], 0
    for p in people:
        for kind, phrase in sweep_phrases(p):
            found = run_query(db, '"%s"' % phrase, None, a.limit)
            for source, date, url, path, page, sha, snip in found:
                print('\t'.join((p['key'], p['label'], kind, phrase, source, date or '', url or path,
                                 str(page or ''), sha, ' '.join(snip.split()))))
            hits += bool(found)
            first = found[0] if found else None
            evidence = ('%s%s' % (first[2] or first[3], ' p.%d' % first[4] if first[4] else '')) if first else a.db
            ledger_rows.append({'person_key': p['key'], 'person_label': p['label'], 'source': 'corpus',
                                'query': '"%s"' % phrase, 'date': time.strftime('%Y-%m-%d'),
                                'result': 'hit' if found else 'none', 'evidence': evidence, 'agent': a.agent})
    if not controls('after'):
        return EXIT_VOID
    if a.ledger:
        add_row = ledger_module().add_row
        for row in ledger_rows:
            add_row(a.ledger, row)
    print('%d of %d phrases found; %s' % (hits, len(ledger_rows), 'ledger rows written to %s' % a.ledger
                                           if a.ledger else 'no ledger (--ledger not given)'), file=sys.stderr)
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
        pages = pdf_pages(path)
        if pages is None:
            raise BadInput('%s is a PDF and pdftotext (poppler) is not on PATH' % path)
    else:
        try:
            pages = read_text(path).split('\f')
        except OSError as e:
            raise BadInput('%s: %s' % (path, e))
    return [' %s ' % rules.fold(flip_headings(re.sub(r'(\w)-\n\s*(\w)', r'\1\2', p))) for p in pages]


def context(page, needle, width=100):
    k = page.find(needle)
    return page[max(0, k - width):k + len(needle) + width].strip()


def xref(pages, people, max_pair_pages=6, max_surname_people=40):
    """[(kind, person, page number, context)]: name, pair and place matches, at most one per person and page."""
    carriers = collections.Counter()
    for p in people:
        for s in set(words(p['surname'])):
            carriers[s] += 1
    pair_pages, out = {}, []
    for p in people:
        given, sur = words(p['given']), words(p['surname'])
        if not given or not sur:
            continue
        done = set()

        def hit(kind, i, needle):
            if i not in done:
                done.add(i)
                out.append((kind, p, i, context(pages[i - 1], needle)))

        name = ' %s ' % ' '.join(given + sur)
        for i, page in enumerate(pages, 1):
            if name in page:
                hit('name', i, name.strip())
        if len(sur) >= 2:
            pair = ' %s ' % ' '.join(sur[-2:])
            if pair not in pair_pages:
                pair_pages[pair] = [i for i, page in enumerate(pages, 1) if pair in page]
            if len(pair_pages[pair]) <= max_pair_pages:
                for i in pair_pages[pair]:
                    hit('pair', i, pair.strip())
        towns = [' %s ' % rules.fold(t) for t in p.get('towns') or [] if rules.fold(t)]
        for s in sur:
            if carriers[s] > max_surname_people:
                continue
            for i, page in enumerate(pages, 1):
                if ' %s ' % s in page and any(t in page for t in towns):
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
    found = xref(pages, people, a.max_pair_pages, a.max_surname_people)
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
        print('%d PDFs not read: pdftotext (poppler) is not on PATH' % n['no_pdftotext'])
    for source, count in db.execute('SELECT source, count(*) FROM meta GROUP BY source ORDER BY source'):
        print('  %-8s %8d rows' % (source, count))
    return 0


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
    s.add_argument('--limit', type=int, default=20)
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
    w.add_argument('--limit', type=int, default=20, help='hits listed per phrase')
    x = sub.add_parser('xref', help='a book or gazette against a dump, as gwt-rank candidates')
    x.add_argument('file', metavar='TEXT_OR_PDF')
    x.add_argument('--dump', required=True)
    x.add_argument('--max-pair-pages', type=int, default=6)
    x.add_argument('--max-surname-people', type=int, default=40)
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
