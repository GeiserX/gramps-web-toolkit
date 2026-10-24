#!/usr/bin/env python3
"""gwt-import-excel: turn rows of a research workbook (.xlsx) into a gwt-apply change list.

It never writes to Gramps. It reads the workbook with the standard library only, maps columns with a JSON file,
checks each row against a dump of the tree, and writes a change list (JSONL) for `gwt-apply`, plus a report.
Rows that look like people already in the tree are listed as possible duplicates and are left out of the change list.

  gwt-import-excel BOOK.xlsx --mapping map.json [--tree dump.json[.gz]] [--out changes.jsonl] [--report report.json]
"""
import argparse
import datetime
import gzip
import hashlib
import json
import re
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
RELNS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
MONTHS = {m: i + 1 for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split())}
GENDER = {'m': 'M', 'h': 'M', 'male': 'M', 'f': 'F', 'female': 'F', 'mujer': 'F', 'hombre': 'M', 'u': 'U', '': 'U'}


class ImportError_(Exception):
    pass


def col_index(ref):
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


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
    root = ET.fromstring(z.read('xl/styles.xml'))
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
    {(sheet, row, col): set of 'formula' / 'error' / 'date' / 'time'} for those cells.
    """
    z = zipfile.ZipFile(path)
    date_styles, time_styles = date_style_ids(z)
    wbroot = ET.fromstring(z.read('xl/workbook.xml'))
    pr = wbroot.find('m:workbookPr', NS)
    date1904 = pr is not None and pr.get('date1904') in ('1', 'true')
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS):
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % NS['m'])))
    rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels')).iter(RELNS + 'Relationship')}
    out = {}
    for sh in wbroot.find('m:sheets', NS):
        target = rels[sh.get('{%s}id' % NS['r'])].lstrip('/')
        name = target if target.startswith('xl/') else 'xl/' + target
        rows = []
        for row in ET.fromstring(z.read(name)).iter('{%s}row' % NS['m']):
            cells = {}
            for c in row.findall('m:c', NS):
                t = c.get('t')
                v = c.find('m:v', NS)
                if t == 's' and v is not None:
                    val = shared[int(v.text)]
                elif t == 'inlineStr':
                    val = ''.join(x.text or '' for x in c.iter('{%s}t' % NS['m']))
                elif t == 'e':
                    val = ''
                    if meta is not None:
                        meta.setdefault((sh.get('name'), int(row.get('r')), col_index(c.get('r'))), set()).add('error')
                elif v is not None:
                    val = v.text or ''
                    if t in (None, 'n') and c.get('s') is not None and int(c.get('s')) in time_styles and meta is not None:
                        meta.setdefault((sh.get('name'), int(row.get('r')), col_index(c.get('r'))), set()).add('time')   # kept as typed, never a date
                    if t in (None, 'n') and c.get('s') is not None and int(c.get('s')) in date_styles:
                        iso = serial_to_iso(val, date1904)
                        val = iso or val
                        if iso and meta is not None:
                            meta.setdefault((sh.get('name'), int(row.get('r')), col_index(c.get('r'))), set()).add('date')
                else:
                    val = ''
                if c.find('m:f', NS) is not None and meta is not None:
                    meta.setdefault((sh.get('name'), int(row.get('r')), col_index(c.get('r'))), set()).add('formula')
                cells[col_index(c.get('r'))] = val.strip()
            n = int(row.get('r'))
            while len(rows) < n:
                rows.append([])
            rows[n - 1] = [cells.get(i, '') for i in range(max(cells) + 1)] if cells else []
        out[sh.get('name')] = rows
    return out


def norm(text):
    t = unicodedata.normalize('NFKD', text or '')
    return ' '.join(re.sub(r'[^a-z0-9 ]+', ' ', ''.join(c for c in t if not unicodedata.combining(c)).lower()).split())


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
    low = s.lower().rstrip('.')
    prefix = ''
    m = re.match(r'^(circa|ca\.?|c\.?|abt|about|~)\s*(.+)$', low)
    if m:
        prefix, low = 'abt ', m.group(2)
    m = re.match(r'^(bef|before|aft|after)\.?\s+(.+)$', low)
    if m:
        prefix, low = {'bef': 'bef ', 'before': 'bef ', 'aft': 'aft ', 'after': 'aft '}[m.group(1)], m.group(2)
    if re.fullmatch(r'\d{4}', low):
        return (prefix + low, None) if int(low) >= 1 else (None, 'impossible date')
    m = re.fullmatch(r'(\d{4})-(\d{1,2})(?:-(\d{1,2}))?', low)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        d = int(m.group(3)) if m.group(3) is not None else 0
        if valid_ymd(y, mo, d) and (m.group(3) is None or d):    # an explicit 00 is not 'unknown'
            return prefix + ('%04d-%02d-%02d' % (y, mo, d) if d else '%04d-%02d' % (y, mo)), None
        return None, 'impossible date'
    m = re.fullmatch(r'(\d{1,2})[/.](\d{1,2})[/.](\d{4})', low)   # day first, as in Spanish and French sources
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if valid_ymd(y, mo, d) and d:
            return prefix + '%04d-%02d-%02d' % (y, mo, d), None
        return None, 'impossible date'
    m = re.fullmatch(r'(?:(\d{1,2})\s+)?([a-z]{3})[a-z]*\.?\s+(\d{4})', low)
    if m and m.group(2) in MONTHS:
        d, y, mo = int(m.group(1) or 0), int(m.group(3)), MONTHS[m.group(2)]
        if not valid_ymd(y, mo, d) or (m.group(1) is not None and d == 0):
            return None, 'impossible date'
        return prefix + ('%04d-%02d-%02d' % (y, mo, d) if d else '%04d-%02d' % (y, mo)), None
    return None, 'unreadable date'


def exact_key(prefix, text):
    """Key from the exact text: the readable slug can collide ('A-B' and 'A B'), the hash of the text cannot."""
    return '%s-%s-%s' % (prefix, slug(text)[:40], hashlib.sha256(text.strip().encode('utf-8')).hexdigest()[:8])


def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', norm(text)).strip('-') or 'x'


def event_year(date):
    """Year of a Gramps date dict: dateval is [day, month, year, slash, ...]; a bare 'year' key is only a fallback."""
    if not isinstance(date, dict):
        return None
    dv = date.get('dateval')
    cand = dv[2] if isinstance(dv, (list, tuple)) and len(dv) >= 3 else (date.get('year') if dv is None else None)
    return cand if isinstance(cand, int) and not isinstance(cand, bool) and cand > 0 else None


def tree_people(path):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        objs = json.load(f)['objects']
    events = {e['handle']: e for e in objs.get('events', [])}
    people = []
    for p in objs.get('people', []):
        n = p.get('primary_name', {})
        sur = ' '.join(s.get('surname', '') for s in n.get('surname_list', []))
        by = None
        i = p.get('birth_ref_index', -1)
        if 0 <= i < len(p.get('event_ref_list', [])):
            ev = events.get(p['event_ref_list'][i]['ref'])
            if ev:
                by = event_year(ev.get('date'))
        people.append({'id': p.get('gramps_id'), 'name': norm(n.get('first_name', '') + ' ' + sur), 'by': by})
    return people


def find_dupes(name, by, people):
    out = []
    for p in people:
        if p['name'] == name and (by is None or p['by'] is None or abs(p['by'] - by) <= 1):
            out.append(p['id'])
    return out


def import_rows(book, mapping, people, meta=None):
    meta = meta or {}
    counted = set()
    ruling = mapping['ruling']
    ops, report = [], {'rows_read': 0, 'people': 0, 'events': 0, 'duplicates': [], 'skipped': [], 'no_source': [], 'date_problems': [], 'formula_cells': [], 'error_cells': [], 'time_cells': [], 'excel_dates': 0}
    sources_done = set()
    seen = {}
    for spec in mapping['sheets']:
        rows = book.get(spec['sheet'])
        if rows is None:
            raise ImportError_('sheet %r not in workbook (has: %s)' % (spec['sheet'], ', '.join(book)))
        hr = spec.get('header_row', 1) - 1
        header = rows[hr] if hr < len(rows) else []
        idx = {h: i for i, h in enumerate(header)}
        cols = spec['columns']
        for k, h in cols.items():
            if h not in idx:
                raise ImportError_('sheet %r has no column %r (mapped as %s); header is %s' % (spec['sheet'], h, k, header))

        cur = {}

        def get(row, key):
            h = cols.get(key)
            if not h:
                return ''
            marks = meta.get((spec['sheet'], cur['n'], idx[h]), set())
            if (spec['sheet'], cur['n'], idx[h]) in counted:
                marks = set()                     # each cell is reported once
            counted.add((spec['sheet'], cur['n'], idx[h]))
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
            _, by_s, _ = read_date(row, 'birth_date')
            by = int(re.search(r'\d{4}', by_s).group(0)) if by_s else None
            nm = norm(first + ' ' + sur)
            d = find_dupes(nm, by, people)
            if d:
                report['duplicates'].append({'row': where, 'name': first + ' ' + sur, 'tree_ids': d})
                continue
            key = 'p-%s-%s' % (slug(first + ' ' + sur), by or 'nd')
            if key in seen:
                report['duplicates'].append({'row': where, 'name': first + ' ' + sur, 'same_as_row': seen[key]})
                continue
            seen[key] = where
            gender = GENDER.get(norm(get(row, 'gender')), 'U')
            ops.append({'op': 'add_person', 'key': key, 'ruling': ruling, 'first_name': first, 'surname': sur, 'gender': gender})
            report['people'] += 1
            src, page = get(row, 'source'), get(row, 'page')
            created = []
            for ev, label in (('birth', 'Birth'), ('death', 'Death')):
                raw, ds, why = read_date(row, ev + '_date')
                place = get(row, ev + '_place')
                if why:
                    report['date_problems'].append({'row': where, 'event': label, 'text': raw, 'reason': why})
                if not (ds or place or raw):
                    continue
                e = {'op': 'add_event', 'key': 'e-%s-%s' % (key[2:], ev), 'ruling': ruling, 'person': 'key:' + key, 'type': label}
                if ds:
                    e['date'] = ds
                if raw and why:
                    e['description'] = ('Time/duration value as written: %s' if why.startswith('time') else 'Date as written: %s') % raw   # never stored as a date
                if place:
                    pk = exact_key('pl', place)
                    if pk not in sources_done:
                        sources_done.add(pk)
                        ops.append({'op': 'add_place', 'key': pk, 'ruling': ruling, 'name': place, 'type': 'Unknown'})
                    e['place'] = 'key:' + pk
                ops.append(e)
                created.append(e['key'])
                report['events'] += 1
            if src:
                sk = exact_key('s', src)
                if sk not in sources_done:
                    sources_done.add(sk)
                    ops.append({'op': 'add_source', 'key': sk, 'ruling': ruling, 'title': src})
                ops.append({'op': 'add_citation', 'key': 'c-%s' % key[2:], 'ruling': ruling, 'source': 'key:' + sk,
                            'page': page or '(no page in workbook)',
                            'attach': [{'person': 'key:' + key}] + [{'event': 'key:' + k} for k in created]})
            else:
                report['no_source'].append(where)
                ops.append({'op': 'add_note', 'key': 'n-nosrc-%s' % key[2:], 'ruling': ruling, 'type': 'Research',
                            'text': '[no source] Imported from workbook row %s with no source column filled.' % where,
                            'attach': [{'person': 'key:' + key}]})
    return ops, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('book')
    ap.add_argument('--mapping', required=True)
    ap.add_argument('--tree', help='tree dump (JSON, optionally .gz) to look for people already there')
    ap.add_argument('--out', default='changes.jsonl')
    ap.add_argument('--report')
    a = ap.parse_args(argv)
    try:
        mapping = json.load(open(a.mapping, encoding='utf-8'))
        if not mapping.get('ruling') or not mapping.get('sheets'):
            raise ImportError_('mapping needs "ruling" and "sheets"')
        meta = {}
        ops, rep = import_rows(read_workbook(a.book, meta), mapping, tree_people(a.tree) if a.tree else [], meta)
    except (ImportError_, KeyError, zipfile.BadZipFile, OSError, ValueError) as e:
        print('gwt-import-excel: %s' % e, file=sys.stderr)
        return 2
    with open(a.out, 'w', encoding='utf-8') as f:
        for o in ops:
            f.write(json.dumps(o, ensure_ascii=False) + '\n')
    if a.report:
        json.dump(rep, open(a.report, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('rows %d | people %d | events %d | possible duplicates %d (left out) | skipped %d | no source %d | date problems %d | formula cells (cached values, not recalculated) %d | error cells %d | time/duration cells %d -> %s'
          % (rep['rows_read'], rep['people'], rep['events'], len(rep['duplicates']), len(rep['skipped']),
             len(rep['no_source']), len(rep['date_problems']), len(rep['formula_cells']), len(rep['error_cells']), len(rep['time_cells']), a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
