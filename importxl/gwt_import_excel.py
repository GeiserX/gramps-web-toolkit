#!/usr/bin/env python3
"""gwt-import-excel: turn rows of a research workbook (.xlsx) into a gwt-apply change list for review.

It never writes to Gramps. It reads the workbook with the standard library only, maps columns with a JSON file,
and writes a change list (JSONL) for `gwt-apply` plus a report. It does not decide who is a duplicate: gwt-apply's
preflight does. The report lists every namesake it sees in the tree dump and the workbook, and a row only gets
`allow_namesake` when the workbook's reason column says why it is another person.

  gwt-import-excel BOOK.xlsx --mapping map.json --batch NAME --out changes.jsonl [--tree dump.json[.gz]] [--report report.json]
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
RELNS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
MONTH_NAMES = {   # English, Spanish and French, full names and the usual abbreviations, accents folded
    1: 'jan january ene enero janv janvier', 2: 'feb february febrero fev fevr fevrier',
    3: 'mar march marzo mars', 4: 'apr april abr abril avr avril', 5: 'may mayo mai',
    6: 'jun june junio juin', 7: 'jul july julio juil juillet', 8: 'aug august ago agosto aout',
    9: 'sep sept september septiembre set setiembre septembre', 10: 'oct october octubre octobre',
    11: 'nov november noviembre novembre', 12: 'dec december dic diciembre decembre'}
MONTHS = {w: n for n, words in MONTH_NAMES.items() for w in words.split()}
GENDER = {'m': 'M', 'h': 'M', 'v': 'M', 'male': 'M', 'man': 'M', 'hombre': 'M', 'varon': 'M', 'masculino': 'M',
          'homme': 'M', 'masculin': 'M',
          'f': 'F', 'female': 'F', 'woman': 'F', 'mujer': 'F', 'hembra': 'F', 'femenino': 'F', 'femme': 'F',
          'feminin': 'F', 'u': 'U', '?': 'U', '': 'U'}
MAX_MEMBER = 100 * 1024 * 1024       # bytes of one unpacked workbook part: more is a zip bomb, not research
MAX_ROW, MAX_COL = 1048576, 16384    # Excel's own limits
LIVING_YEARS = 100                   # born this recently with no death: maybe alive, held unless --allow-living
GRAMPS_ID = re.compile(r'^[A-Za-z]+\d+(?:-[A-Za-z0-9]+)?$')   # as gwt-apply accepts them
BATCH = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')


class MappingError(Exception):
    pass


def col_index(ref):
    m = re.match(r'[A-Z]+', ref or '')
    if not m:
        raise MappingError('cell reference %r has no column' % ref)
    n = 0
    for ch in m.group(0):
        n = n * 26 + ord(ch) - 64
    if n > MAX_COL:
        raise MappingError('cell reference %r is past column %d' % (ref, MAX_COL))
    return n - 1


def read_member(z, name):
    """One part of the workbook, refused past MAX_MEMBER bytes. zipfile never unpacks more than the size
    the header declares (a lying header ends in a CRC error), so checking that size is enough."""
    if z.getinfo(name).file_size > MAX_MEMBER:
        raise MappingError('%s unpacks to more than %d MB' % (name, MAX_MEMBER // 2 ** 20))
    return z.read(name)


BUILTIN_DATE_FMT = {14, 15, 16, 17, 22, 27, 28, 29, 30, 31, 34, 35, 36} | set(range(50, 59))   # carry a calendar date
BUILTIN_TIME_FMT = {18, 19, 20, 21, 32, 33, 45, 46, 47}                                        # time of day or duration only


def classify_format(code):
    """'date' for a format that shows a calendar date, 'time' for time of day or elapsed time only, else None."""
    c = re.sub(r'"[^"]*"|\\.|_.|\*.', '', code.lower())
    elapsed = bool(re.search(r'\[(h+|m+|s+)\]', c))
    c = re.sub(r'\[[^\]]*\]', '', c)
    if not elapsed and c.strip() in ('', 'general'):
        return None
    has_time = elapsed or bool(re.search(r'[hs]', c))
    if re.search(r'[dy]', c) or (re.search(r'm', c) and not has_time):
        return 'date'
    return 'time' if has_time else None


def date_style_ids(z):
    """(date styles, time-only styles): indexes of cell styles (s="n") by what their number format shows."""
    if 'xl/styles.xml' not in z.namelist():
        return set(), set()
    root = ET.fromstring(read_member(z, 'xl/styles.xml'))
    custom = {}
    nf = root.find('m:numFmts', NS)
    if nf is not None:
        for f in nf:
            custom[int(f.get('numFmtId'))] = classify_format(f.get('formatCode', ''))
    dates, times = set(), set()
    xfs = root.find('m:cellXfs', NS)
    for i, xf in enumerate(xfs if xfs is not None else []):
        fid = int(xf.get('numFmtId', 0))
        kind = 'date' if fid in BUILTIN_DATE_FMT else 'time' if fid in BUILTIN_TIME_FMT else custom.get(fid)
        if kind == 'date':
            dates.add(i)
        elif kind == 'time':
            times.add(i)
    return dates, times


def serial_to_iso(text, date1904=False):
    """Excel date serial to YYYY-MM-DD (whole days only). None if it is not a plausible date serial."""
    try:
        v = float(text)
    except ValueError:
        return None
    if v != int(v) or not (0 if date1904 else 1) <= v < 2958466:
        return None
    n = int(v)
    if date1904:
        base, off = datetime.date(1904, 1, 1), n
    else:
        if n == 60:
            return None                      # Excel's invented 29 Feb 1900
        base, off = datetime.date(1899, 12, 31), n - (1 if n > 60 else 0)
    try:
        return (base + datetime.timedelta(days=off)).isoformat()
    except OverflowError:
        return None


def read_workbook(path, meta=None):
    """{sheet name: [row, ...]} where a row is a list of strings ('' for empty cells).

    Cells with a date style hold an ISO date. Formula cells give their cached value. `meta`, when a dict, gets
    {(sheet, row, col): set of 'formula' / 'error' / 'date' / 'time'} for those cells. A cell or row without its
    optional `r` attribute takes the next position, as Excel reads it.
    """
    with zipfile.ZipFile(path) as z:
        return _read_workbook(z, meta)


def _read_workbook(z, meta):
    date_styles, time_styles = date_style_ids(z)
    wbroot = ET.fromstring(read_member(z, 'xl/workbook.xml'))
    pr = wbroot.find('m:workbookPr', NS)
    date1904 = pr is not None and pr.get('date1904') in ('1', 'true')
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(read_member(z, 'xl/sharedStrings.xml')).findall('m:si', NS):
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % NS['m'])))
    rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(read_member(z, 'xl/_rels/workbook.xml.rels')).iter(RELNS + 'Relationship')}
    out = {}
    for sh in wbroot.find('m:sheets', NS):
        target = rels[sh.get('{%s}id' % NS['r'])].lstrip('/')
        name = target if target.startswith('xl/') else 'xl/' + target
        sheet = sh.get('name')
        rows = []
        n = 0
        for row in ET.fromstring(read_member(z, name)).iter('{%s}row' % NS['m']):
            n = int(row.get('r')) if row.get('r') else n + 1
            if not 1 <= n <= MAX_ROW:
                raise MappingError('sheet %r has a row numbered %d (Excel stops at %d)' % (sheet, n, MAX_ROW))
            cells = {}
            col = -1
            for c in row.findall('m:c', NS):
                col = col_index(c.get('r')) if c.get('r') else col + 1
                if col >= MAX_COL:
                    raise MappingError('sheet %r row %d goes past column %d' % (sheet, n, MAX_COL))
                where = (sheet, n, col)
                t = c.get('t')
                v = c.find('m:v', NS)
                if t == 's' and v is not None:
                    val = shared[int(v.text)]
                elif t == 'inlineStr':
                    val = ''.join(x.text or '' for x in c.iter('{%s}t' % NS['m']))
                elif t == 'e':
                    val = ''
                    if meta is not None:
                        meta.setdefault(where, set()).add('error')
                elif v is not None:
                    val = v.text or ''
                    if t in (None, 'n') and c.get('s') is not None and int(c.get('s')) in time_styles and meta is not None:
                        meta.setdefault(where, set()).add('time')   # kept as typed, never a date
                    if t in (None, 'n') and c.get('s') is not None and int(c.get('s')) in date_styles:
                        iso = serial_to_iso(val, date1904)
                        val = iso or val
                        if iso and meta is not None:
                            meta.setdefault(where, set()).add('date')
                else:
                    val = ''
                if c.find('m:f', NS) is not None and meta is not None:
                    meta.setdefault(where, set()).add('formula')
                cells[col] = val.strip()
            if len(rows) < n:
                rows.extend([()] * (n - len(rows)))      # one shared empty tuple per missing row, not a list each
            rows[n - 1] = [cells.get(i, '') for i in range(max(cells) + 1)] if cells else []
        out[sheet] = rows
    return out


def norm(text):
    t = unicodedata.normalize('NFKD', text or '')
    return ' '.join(re.sub(r'[^a-z0-9 ?]+', ' ', ''.join(c for c in t if not unicodedata.combining(c)).lower()).split())


def fold(text):
    """gwt-apply's name comparison: no case, accents or extra spaces ("Agüera" == "aguera")."""
    t = unicodedata.normalize('NFKD', text or '')
    return ' '.join(''.join(c for c in t if not unicodedata.combining(c)).casefold().split())


def loose(first, surname):
    """gwt-apply preflight's looser name: first given name and first surname."""
    f, s = fold(first).split(), fold(surname).split()
    return (f[0], s[0]) if f and s else None


def valid_ymd(y, mo, d):
    """Real calendar check (leap years included). d == 0 means day unknown, mo == 0 never allowed here."""
    if not 1 <= mo <= 12 or y < 1:
        return False
    if d == 0:
        return True
    try:
        datetime.date(y, mo, d)
        return True
    except ValueError:
        return False


def parse_date(raw):
    """Return (gwt-apply date string or None, reason when unreadable). Never guesses a missing part."""
    s = (raw or '').strip()
    if not s or s in ('?', '??', 's/f', 'n/d'):
        return None, None
    low = fold(s).rstrip('.')
    prefix = ''
    m = re.match(r'^(circa|ca\.?|c\.?|abt|about|~|hacia|h\.|vers)\s*(.+)$', low)
    if m:
        prefix, low = 'abt ', m.group(2)
    m = re.match(r'^(bef|before|antes de|avant|aft|after|despues de|apres)\.?\s+(.+)$', low)
    if m:
        prefix, low = ('bef ' if m.group(1) in ('bef', 'before', 'antes de', 'avant') else 'aft '), m.group(2)
    if re.fullmatch(r'\d{4}', low):
        return (prefix + low, None) if int(low) >= 1 else (None, 'impossible date')
    m = re.fullmatch(r'(\d{4})-(\d{1,2})(?:-(\d{1,2}))?', low)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        d = int(m.group(3)) if m.group(3) is not None else 0
        if valid_ymd(y, mo, d) and (m.group(3) is None or d):    # an explicit 00 is not 'unknown'
            return prefix + ('%04d-%02d-%02d' % (y, mo, d) if d else '%04d-%02d' % (y, mo)), None
        return None, 'impossible date'
    m = re.fullmatch(r'(\d{1,2})([/.-])(\d{1,2})\2(\d{4})', low)   # day first, as in Spanish and French sources
    if m:
        d, mo, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
        if valid_ymd(y, mo, d) and d:
            return prefix + '%04d-%02d-%02d' % (y, mo, d), None
        return None, 'impossible date'
    m = re.fullmatch(r'(?:(\d{1,2})(?:er|º|o)?\s+(?:de\s+)?)?([a-z]+)\.?,?\s+(?:de\s+)?(\d{4})', low)
    if m and m.group(2) in MONTHS:
        d, y, mo = int(m.group(1) or 0), int(m.group(3)), MONTHS[m.group(2)]
        if not valid_ymd(y, mo, d) or (m.group(1) is not None and d == 0):
            return None, 'impossible date'
        return prefix + ('%04d-%02d-%02d' % (y, mo, d) if d else '%04d-%02d' % (y, mo)), None
    return None, 'unreadable date'


def day_month_ambiguous(raw):
    """True for 03/04/1901: day first was assumed, and month first would also be a date."""
    m = re.search(r'(?<!\d)(\d{1,2})([/.-])(\d{1,2})\2\d{4}', raw or '')
    return bool(m) and int(m.group(1)) <= 12 and int(m.group(3)) <= 12 and m.group(1).lstrip('0') != m.group(3).lstrip('0')


def short_hash(text):
    return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()[:8]


def exact_key(prefix, text):
    """Key from the exact text: the readable slug can collide ('A-B' and 'A B'), the hash of the text cannot."""
    return '%s-%s-%s' % (prefix, slug(text)[:40], short_hash(text))


def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', norm(text)).strip('-') or 'x'


def _objects(objs, *names):
    for n in names:
        if n in objs:
            return objs[n] or []
    return []


def read_tree(path):
    """People and places of a tree dump: a gwt-check dump or a gwt-apply snapshot (JSON, optionally .gz).

    Returns ({'full': {folded name: [ids]}, 'loose': {(first given, first surname): [ids]}}, {folded place name: [ids]}).
    """
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        objs = json.load(f)['objects']
    people = {'full': {}, 'loose': {}}
    for p in _objects(objs, 'people', 'Person'):
        n = p.get('primary_name') or {}
        first = n.get('first_name', '')
        sur = ' '.join(s.get('surname', '') for s in n.get('surname_list') or [])
        pid = p.get('gramps_id') or p.get('handle')
        people['full'].setdefault((fold(first), fold(sur)), []).append(pid)
        if loose(first, sur):
            people['loose'].setdefault(loose(first, sur), []).append(pid)
    places = {}
    for pl in _objects(objs, 'places', 'Place'):
        name = (pl.get('name') or {}).get('value') if isinstance(pl.get('name'), dict) else pl.get('title')
        if name:
            places.setdefault(fold(name), []).append(pl.get('gramps_id') or pl.get('handle'))
    return people, places


def import_rows(book, mapping, tree=None, meta=None, batch='b', this_year=None, allow_living=False):
    """(ops, report). `tree` is what read_tree returns, or None."""
    meta = meta or {}
    people, places = tree or ({'full': {}, 'loose': {}}, {})
    this_year = this_year or datetime.date.today().year
    counted = set()
    ruling = mapping['ruling']
    ops, report = [], {'rows_read': 0, 'people': 0, 'events': 0, 'namesakes': [], 'living_held': [], 'skipped': [],
                       'no_source': [], 'date_problems': [], 'day_first_assumed': [], 'gender_unread': [],
                       'places_unmatched': [], 'places_ambiguous': [], 'place_ids_unknown': [],
                       'namesake_reason_unused': [], 'formula_cells': [], 'error_cells': [], 'time_cells': [],
                       'excel_dates': 0}
    sources_done = set()
    made = {'full': {}, 'loose': {}}           # people this import makes so far, by name, as key:<key>
    for spec in mapping['sheets']:
        rows = book.get(spec['sheet'])
        if rows is None:
            raise MappingError('sheet %r not in workbook (has: %s)' % (spec['sheet'], ', '.join(book)))
        hr = spec.get('header_row', 1) - 1
        header = list(rows[hr]) if hr < len(rows) else []
        dup = sorted({h for h in header if h and header.count(h) > 1})
        cols = spec['columns']
        if dup and any(h in dup for h in cols.values()):
            raise MappingError('sheet %r has the column %s more than once' % (spec['sheet'], ', '.join(map(repr, dup))))
        idx = {h: i for i, h in enumerate(header)}
        for k, h in cols.items():
            if h not in idx:
                raise MappingError('sheet %r has no column %r (mapped as %s); header is %s' % (spec['sheet'], h, k, header))

        cur = {}

        def get(row, key):
            h = cols.get(key)
            if not h:
                return ''
            cell = (spec['sheet'], cur['n'], idx[h])
            marks = set() if cell in counted else meta.get(cell, set())   # each cell is reported once
            counted.add(cell)
            label = '%s!%d:%s' % (spec['sheet'], cur['n'], h)
            if 'formula' in marks:                # also when the cell is a date or an error
                report['formula_cells'].append(label)
            if 'time' in marks:                   # time or duration format: the number is not a date
                report['time_cells'].append(label)
            if 'error' in marks:
                report['error_cells'].append(label)
            if 'date' in marks:
                report['excel_dates'] += 1
            return row[idx[h]].strip() if idx[h] < len(row) else ''

        def read_date(row, key):
            """(raw text, gwt-apply date or None, problem or None). A time or duration cell is never parsed as a date."""
            raw = get(row, key)
            h = cols.get(key)
            if raw and h and 'time' in meta.get((spec['sheet'], cur['n'], idx[h]), set()):
                return raw, None, 'time or duration format, not a date'
            ds, why = parse_date(raw)
            return raw, ds, why

        for n, row in enumerate(rows[hr + 1:], start=hr + 2):
            cur['n'] = n
            if not any(row):
                continue
            report['rows_read'] += 1
            where = '%s!%d' % (spec['sheet'], n)
            first, sur = get(row, 'first_name'), get(row, 'surname')
            if not first or not sur:
                report['skipped'].append({'row': where, 'reason': 'name incomplete'})
                continue
            name = first + ' ' + sur
            dates = {ev: read_date(row, ev + '_date') for ev in ('birth', 'death')}
            spots = {ev: (get(row, ev + '_place'), get(row, ev + '_place_id')) for ev in ('birth', 'death')}
            by = re.search(r'\d{4}', dates['birth'][1]) if dates['birth'][1] else None
            died = dates['death'][1] or dates['death'][2] or any(spots['death'])
            if by and int(by.group(0)) > this_year - LIVING_YEARS and not died and not allow_living:
                report['living_held'].append({'row': where, 'name': name, 'born': dates['birth'][1]})
                continue
            # one key per workbook batch, sheet, row and name: a row that moves gets a new key, never another's
            key = 'xl-%s-%s-r%d-%s-%s' % (batch, slug(spec['sheet'])[:20], n, slug(name)[:30], short_hash(fold(name)))
            full, lo = (fold(first), fold(sur)), loose(first, sur)
            tree_ids = sorted(set(people['full'].get(full, []) + (people['loose'].get(lo, []) if lo else [])))
            rows_same = [r for r in made['full'].get(full, []) + (made['loose'].get(lo, []) if lo else [])]
            rows_same = list(dict.fromkeys(rows_same))
            reason = get(row, 'namesake_reason')
            person = {'op': 'add_person', 'key': key, 'ruling': ruling, 'first_name': first, 'surname': sur}
            gtext = get(row, 'gender')
            gender = GENDER.get(norm(gtext))
            if gender is None:
                report['gender_unread'].append({'row': where, 'text': gtext})
                gender = 'U'
            person['gender'] = gender
            if tree_ids or rows_same:
                report['namesakes'].append({'row': where, 'name': name, 'tree_ids': tree_ids,
                                            'rows': [r[4:] for r in rows_same], 'reason': reason or None})
                if reason:
                    person.update(allow_namesake=True, namesake_reason=reason, namesake_of=tree_ids + rows_same)
            elif reason:
                report['namesake_reason_unused'].append({'row': where, 'name': name})
            ops.append(person)
            made['full'].setdefault(full, []).append('key:' + key)
            if lo:
                made['loose'].setdefault(lo, []).append('key:' + key)
            report['people'] += 1
            src, page = get(row, 'source'), get(row, 'page')
            created = []
            for ev, label in (('birth', 'Birth'), ('death', 'Death')):
                raw, ds, why = dates[ev]
                text, pid = spots[ev]
                if why:
                    report['date_problems'].append({'row': where, 'event': label, 'text': raw, 'reason': why})
                if ds and day_month_ambiguous(raw):
                    report['day_first_assumed'].append({'row': where, 'event': label, 'text': raw, 'read_as': ds})
                if not (ds or why or text or pid):          # '?' alone states nothing
                    continue
                e = {'op': 'add_event', 'key': '%s-%s' % (key, ev), 'ruling': ruling, 'person': 'key:' + key, 'type': label}
                if ds:
                    e['date'] = ds
                notes = []
                if raw and why:
                    notes.append(('Time/duration value as written: %s' if why.startswith('time') else 'Date as written: %s') % raw)
                place = None
                if pid:
                    if GRAMPS_ID.match(pid) and (tree is None or any(pid in v for v in places.values())):
                        place = pid
                    else:
                        report['place_ids_unknown'].append({'row': where, 'event': label, 'id': pid})
                elif text:
                    found = places.get(fold(text), [])
                    if len(found) == 1:
                        place = found[0]
                    elif found:
                        report['places_ambiguous'].append({'row': where, 'event': label, 'text': text, 'tree_ids': found})
                    else:
                        report['places_unmatched'].append({'row': where, 'event': label, 'text': text})
                if place:
                    e['place'] = place
                elif text:
                    notes.append('Place as written: %s' % text)   # never a new place: the tree has a hierarchy
                if notes:
                    e['description'] = '; '.join(notes)
                ops.append(e)
                created.append(e['key'])
                report['events'] += 1
            if src:
                sk = exact_key('xl-%s-s' % batch, src)
                if sk not in sources_done:
                    sources_done.add(sk)
                    ops.append({'op': 'add_source', 'key': sk, 'ruling': ruling, 'title': src})
                ops.append({'op': 'add_citation', 'key': key + '-cit', 'ruling': ruling, 'source': 'key:' + sk,
                            'page': page or '(no page in workbook, row %s)' % where,
                            'attach': [{'person': 'key:' + key}] + [{'event': 'key:' + k} for k in created]})
            else:
                report['no_source'].append(where)
                ops.append({'op': 'add_note', 'key': key + '-nosrc', 'ruling': ruling, 'type': 'Research',
                            'text': '[no source] Imported from workbook row %s with no source column filled.' % where,
                            'attach': [{'person': 'key:' + key}]})
    return ops, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('book')
    ap.add_argument('--mapping', required=True)
    ap.add_argument('--batch', required=True,
                    help='name for this workbook (a-z, 0-9, -), part of every key; keep it the same on reruns')
    ap.add_argument('--out', required=True)
    ap.add_argument('--force', action='store_true', help='overwrite --out and --report')
    ap.add_argument('--tree', help='tree dump or gwt-apply snapshot (JSON, optionally .gz): namesakes and places')
    ap.add_argument('--report')
    ap.add_argument('--allow-living', action='store_true',
                    help='import people born less than %d years ago with no death' % LIVING_YEARS)
    a = ap.parse_args(argv)
    try:
        if not BATCH.match(a.batch):
            raise MappingError('--batch %r: use 1-40 of a-z, 0-9 and -, starting with a letter or digit' % a.batch)
        for p in (a.out, a.report):
            if p and os.path.exists(p) and not a.force:
                raise MappingError('%s exists; pass --force to overwrite it' % p)
        with open(a.mapping, encoding='utf-8') as f:
            mapping = json.load(f)
        if not mapping.get('ruling') or not mapping.get('sheets'):
            raise MappingError('mapping needs "ruling" and "sheets"')
        meta = {}
        ops, rep = import_rows(read_workbook(a.book, meta), mapping, read_tree(a.tree) if a.tree else None, meta,
                               batch=a.batch, allow_living=a.allow_living)
    except (MappingError, KeyError, zipfile.BadZipFile, OSError, ValueError, TypeError, IndexError, ET.ParseError) as e:
        print('gwt-import-excel: %s: %s' % (type(e).__name__, e), file=sys.stderr)
        return 2
    with open(a.out, 'w', encoding='utf-8') as f:
        for o in ops:
            f.write(json.dumps(o, ensure_ascii=False) + '\n')
    if a.report:
        with open(a.report, 'w', encoding='utf-8') as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    unsettled = sum(1 for x in rep['namesakes'] if not x['reason'])
    print('rows %d | people %d | events %d | namesakes %d (%d with no reason: gwt-apply will refuse the list) | '
          'held as maybe living %d | skipped %d | no source %d | date problems %d | places unmatched %d, ambiguous %d | '
          'formula cells (cached values, not recalculated) %d | error cells %d | time/duration cells %d -> %s'
          % (rep['rows_read'], rep['people'], rep['events'], len(rep['namesakes']), unsettled, len(rep['living_held']),
             len(rep['skipped']), len(rep['no_source']), len(rep['date_problems']), len(rep['places_unmatched']),
             len(rep['places_ambiguous']), len(rep['formula_cells']), len(rep['error_cells']), len(rep['time_cells']), a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
