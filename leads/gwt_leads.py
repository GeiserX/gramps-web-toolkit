#!/usr/bin/env python3
"""gwt-leads: turn the gaps of a tree dump into research tasks for gwt-queue.

Reads an offline dump (the JSON `gwt-check dump` writes, or a `gwt-apply` snapshot; gzipped or not) and never
touches the tree. Exit 2 on a file that is not a dump, never a silent empty list. Prints the gaps highest priority first: direct ancestors of --root count
100 minus a point per generation (never below 40), everyone else 10.

  gwt-leads [--root I0001] [--kinds branch-end,missing-vital,uncited] [--links FILE] [--archives FILE]
            [--limit N] [--format tsv|json|queue] DUMP

Gaps:
  branch-end     a person with no birth parents recorded: where a line stops
  missing-vital  no birth (or baptism), no marriage (a family with children but no marriage event) or, for people
                 born in --living-after or earlier, no death (or burial); or such an event with no date or no place
  uncited        a birth or death event (or its baptism or burial stand-in) with no citation
  unsupported-link  (only with --links, the report `gwt-check links --json` wrote from the same dump) a birth
                 link whose cited records name neither parent, or only one of two: words only, never a wrong link

Only the person's own events count: refs with role Primary, the birth and death the person points at first.
People who may be living (no death, burial or cremation, and no date of theirs or of a descendant that proves a
birth in --living-after or earlier) and people or events marked private are held back from every format; their Gramps ids go to stderr.

`--format queue` prints one JSON line per task for `gwt-queue add --jsonl - --dedupe`. `--archives FILE` (a TSV
of place -> archive rows) adds where each gap's record is kept and whether that year is online; it never fetches
anything. `--archives builtin:es,fr` (or `builtin:all`) reads the catalogue shipped in leads/archives/ instead, one
TSV per country; `gwt-leads archives probe [--country CC]` checks its addresses (maintainers only). Standard library
only.
"""
import argparse, collections, csv, datetime, gzip, json, os, re, string, sys, time, unicodedata, urllib.parse
import urllib.error, urllib.request, urllib.robotparser

KINDS = ('branch-end', 'missing-vital', 'uncited', 'unsupported-link')
DEFAULT_KINDS = ('branch-end', 'missing-vital', 'uncited')  # unsupported-link needs --links, so it is asked for
LINK_GAPS = {'cited, names neither': 'a cited record names neither parent',
             'a record names one': 'records name only one of the two parents'}
# the other levels gwt-check links writes; a cited link has its own source on the child_ref; 'no record names a
# parent' means no cited birth or baptism at all, which uncited and missing-vital already list
LINK_NOT_GAPS = ('the link itself is cited', 'birth record names both', 'other record names both', 'no record names a parent')
BIRTH_LIKE = ('Birth', 'Baptism', 'Christening')   # in order of preference
DEATH_LIKE = ('Death', 'Burial', 'Cremation')
MARRIAGE_LIKE = ('Marriage',)
FACT_KEYS = {'Birth': 'b', 'Baptism': 'bap', 'Christening': 'bap', 'Death': 'd', 'Burial': 'bur', 'Cremation': 'bur'}
# Gramps date modifiers: 0 none, 1 before, 2 after, 3 about, 4 range, 5 span, 6 text only, 7 from, 8 to
MODIFIER_MARK = {1: '<', 2: '>', 3: '~', 4: '~', 5: '~', 7: '>', 8: '<'}
ABOUT_SLACK = 10  # an "about" or estimated year may be this many years early, for the living test
ARCHIVE_COLUMNS = ('match', 'archive', 'series', 'years', 'url')  # --archives: every one must be in the header
ARCHIVE_OPTIONAL = ('access',)
ACCESS = ('online', 'browser', 'letter', 'unknown')  # an empty access cell reads as unknown
URL_FIELDS = ('place', 'year')
# --archives builtin:CC: the catalogue shipped in leads/archives/<CC>.tsv (XX.tsv: sources for several countries)
BUILTIN = 'builtin:'
CATALOGUE_COLUMNS = ('match', 'match_level', 'archive', 'series', 'record_types', 'years', 'civil_start', 'url',
                     'access', 'robots', 'coverage_places', 'tested', 'notes')
CATALOGUE_LEVELS = ('world', 'country', 'region', 'place')  # world rows match '*', any place at all
CATALOGUE_ACCESS = ('online', 'browser', 'login', 'paid', 'letter', 'in-person')
CATALOGUE_ROBOTS = ('', 'ok', 'disallow', 'challenge')  # empty: not known
RECORD_TYPES = ('birth', 'marriage', 'death', 'census', 'migration', 'military', 'probate', 'land', 'notarial', 'court',
                'press', 'directory', 'tree', 'gazetteer', 'catalogue', 'other')
CATALOGUE_URL_FIELDS = URL_FIELDS + ('surname', 'given', 'code', 'year_from', 'year_to')
YEAR_WINDOW = 5  # {year_from} and {year_to}: this many years either side of {year}
NOTE_HINTS = 3   # a queue note lists at most this many catalogue hints per record
PROBE_AGENT = 'gramps-web-toolkit archives probe (+https://github.com/GeiserX/gramps-web-toolkit)'
PROBE_VALUES = {'place': 'Example', 'year': 1900, 'surname': 'Example', 'given': 'Anna', 'code': '00000',
                'year_from': 1895, 'year_to': 1905}  # invented: a probe never searches for a real person


class BadInput(Exception):
    pass


def default_living_after():
    return datetime.date.today().year - 100


SNAPSHOT_FORMAT = 'gwt-apply-snapshot/1'
# a gwt-apply snapshot keys objects by Gramps class; a gwt-check dump by REST namespace
SNAPSHOT_KEYS = {'Person': 'people', 'Family': 'families', 'Event': 'events', 'Place': 'places'}
ACCEPTED = 'a gwt-check dump or a gwt-apply snapshot'


def load(path):
    """The people, families, events and places of a `gwt-check dump` file ({tree, dumped_at, objects: {people:
    [...]}}) or of a `gwt-apply` snapshot ({format: gwt-apply-snapshot/1, objects: {Person: [...]}}), gzipped or
    not. A bare {people: [...]} map is read too. Anything else is an error, never an empty result."""
    try:
        with open(path, 'rb') as f:
            gz = f.read(2) == b'\x1f\x8b'
        with (gzip.open if gz else open)(path, 'rt', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError, EOFError) as exc:
        raise BadInput('%s: not JSON, so not %s (%s)' % (path, ACCEPTED, exc))
    snapshot = isinstance(data, dict) and data.get('format') == SNAPSHOT_FORMAT
    if isinstance(data, dict):
        data = data.get('objects', data)
    if not isinstance(data, dict):
        raise BadInput('%s: not %s: expected a JSON object with one list per type' % (path, ACCEPTED))
    if snapshot:
        data = {SNAPSHOT_KEYS[k]: v for k, v in data.items() if k in SNAPSHOT_KEYS}
    bad = [t for t in ('people', 'families', 'events') if not isinstance(data.get(t), list)]
    if bad:
        raise BadInput('%s: no %s list: is this %s?' % (path, ', '.join(bad), ACCEPTED))
    if not data['people']:
        raise BadInput('%s: the dump holds no people' % path)
    out = {}
    for t in ('people', 'families', 'events'):
        bad = [o for o in data[t] if not isinstance(o, dict) or 'handle' not in o]
        if bad:
            raise BadInput('%s: %d %s records are not objects with a handle: is this %s?' % (path, len(bad), t, ACCEPTED))
        out[t] = {o['handle']: o for o in data[t]}
    places = data.get('places', [])
    if not isinstance(places, list) or any(not isinstance(o, dict) or 'handle' not in o for o in places):
        raise BadInput('%s: places must be a list of objects with a handle' % path)
    out['places'] = {o['handle']: o for o in places}
    return out


def text_of(value):
    """A Gramps enum as the dump has it: a plain string, or {"string": ...}."""
    return (value.get('string') if isinstance(value, dict) else value) or ''


def etype(ev):
    return text_of(ev.get('type'))


def year(ev):
    """The structured year of an event's date, or None (no date, or text only)."""
    d = ev.get('date') or {}
    if d.get('modifier') == 6:
        return None
    v = d.get('dateval') or []
    y = d.get('year') if isinstance(d.get('year'), int) and d.get('year') else None
    return y or (v[2] if len(v) > 2 and isinstance(v[2], int) and v[2] else None)


def latest_year(ev):
    """The latest year the event can be in, for the living test: the end of a range or span, the year plus
    ABOUT_SLACK for an "about" or estimated date, None for "after" or "from" (no upper bound) or no year."""
    y = year(ev) if ev else None
    if not y:
        return None
    d = ev.get('date') or {}
    m = d.get('modifier') or 0
    if m in (2, 7):
        return None
    if m in (4, 5):
        v = d.get('dateval') or []
        return v[6] if len(v) > 6 and isinstance(v[6], int) and v[6] >= y else y
    if m == 3 or (d.get('quality') or 0):
        return y + ABOUT_SLACK
    return y


def year_text(ev):
    """The year as a search filter: '1860' only for a plain, regular date; '~1860', '<1860' or '>1860' when the
    date is about, a range, before, after or estimated. '' when there is no year."""
    y = year(ev)
    if not y:
        return ''
    d = ev.get('date') or {}
    mark = MODIFIER_MARK.get(d.get('modifier') or 0, '')
    if not mark and (d.get('quality') or 0):
        mark = '~'
    return '%s%d' % (mark, y)


def date_gap(ev):
    """'' if the event has a structured year; else why it has none: no date at all, or text we cannot read as a year."""
    if year(ev):
        return ''
    text = ((ev.get('date') or {}).get('text') or '').strip()
    return 'has date text without an interpretable year (%s)' % text if text else 'has no date'


def place_chain(T, handle):
    """Every level of a place, the place itself first: ['Parish', 'Town', 'Province', 'Country']."""
    names = []
    seen = set()
    while handle and handle not in seen:
        seen.add(handle)
        pl = T.get('places', {}).get(handle) or {}
        n = pl.get('name')
        name = ((n.get('value') if isinstance(n, dict) else n) or pl.get('title') or '').strip()
        if name:
            names.append(name)
        refs = pl.get('placeref_list') or []
        handle = refs[0].get('ref') if refs and isinstance(refs[0], dict) else None
    return names


def place_name(T, handle, levels=2):
    """The place and the one it sits in, 'Parish, Town', from the dump's place hierarchy."""
    return ', '.join(place_chain(T, handle)[:levels])


def fold(text):
    """Names compare folded the way gwt-ledger's normalize folds them: case, accents and punctuation do not count,
    «Saint-Éxemple» == «saint exemple». A copy of that rule until the shared core lands."""
    text = unicodedata.normalize('NFKD', (text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', text))


def parse_years(text):
    """'1600-1792,1850' -> [(1600, 1792), (1850, 1850)]; '' -> []. Anything else is BadInput."""
    out = []
    for part in (x.strip() for x in text.split(',')) if text.strip() else ():
        m = re.fullmatch(r'(\d{1,4})(?:\s*-\s*(\d{1,4}))?', part)
        if not m or int(m.group(2) or m.group(1)) < int(m.group(1)):
            raise BadInput('years %r: write ranges like 1793-1902, comma-separated' % text)
        out.append((int(m.group(1)), int(m.group(2) or m.group(1))))
    return out


def load_archives(path):
    """An --archives TSV as {folded match: row}, the first row of a name kept: a header with match, archive, series,
    years, url (access optional), then one row per place name. Read as plain tab-separated text, like the ledger and
    the queue: a quote is a character, never a field delimiter. A missing, unknown or repeated column, a bad years
    cell, an access value outside ACCESS or a url placeholder other than {place} and {year} is BadInput, never a row
    skipped in silence."""
    try:
        with open(path, encoding='utf-8-sig', newline='') as fh:
            reader = csv.DictReader(fh, delimiter='\t', quoting=csv.QUOTE_NONE)
            header = [h.strip() for h in reader.fieldnames or []]
            reader.fieldnames = header
            rows = list(reader)
    except UnicodeDecodeError:
        raise BadInput('%s: the file is not UTF-8; save it as UTF-8 text, tab-separated' % path)
    except (OSError, csv.Error) as exc:
        raise BadInput('%s: %s' % (path, exc))
    missing = [c for c in ARCHIVE_COLUMNS if c not in header]
    unknown = [c or '(empty)' for c in header if c not in ARCHIVE_COLUMNS + ARCHIVE_OPTIONAL]
    if missing or unknown:
        raise BadInput('%s: %s; the columns are %s, plus %s if you want it' % (
            path, '; '.join(x for x in ('missing column %s' % ', '.join(missing) if missing else '',
                                         'unknown column %s' % ', '.join(unknown) if unknown else '') if x),
            ', '.join(ARCHIVE_COLUMNS), ', '.join(ARCHIVE_OPTIONAL)))
    repeated = sorted({c for c in header if header.count(c) > 1})
    if repeated:
        raise BadInput('%s: column %s appears more than once' % (path, ', '.join(repeated)))
    index = {}
    for n, row in enumerate(rows, 2):
        where = '%s line %d' % (path, n)
        if None in row:
            raise BadInput('%s: the row has more cells than the header' % where)
        row = {k: (v or '').strip() for k, v in row.items()}  # trailing empty cells may be left out
        if not fold(row['match']) or not row['archive']:
            raise BadInput('%s: match and archive must not be empty' % where)
        access = row.get('access') or 'unknown'
        if access not in ACCESS:
            raise BadInput('%s: access %r is not one of %s' % (where, access, ', '.join(ACCESS)))
        try:
            fields = [(f, spec, conv) for _, f, spec, conv in string.Formatter().parse(row['url']) if f is not None]
        except ValueError as exc:
            raise BadInput('%s: url: %s' % (where, exc))
        bad = [f for f, spec, conv in fields if f not in URL_FIELDS or spec or conv]
        if bad:
            raise BadInput('%s: url placeholder {%s} unknown; only {place} and {year}' % (where, bad[0]))
        try:
            years = parse_years(row['years'])
        except BadInput as exc:
            raise BadInput('%s: %s' % (where, exc))
        index.setdefault(fold(row['match']), {'archive': row['archive'], 'series': row['series'], 'years': years,
                                              'url': row['url'], 'access': access})  # the first row of a name wins
    return index


def exact_year(ev):
    """The year to test against online ranges: a plain or "about" (or estimated) year; None for before, after,
    a range or span, text only, or no year."""
    d = ev.get('date') or {}
    return year(ev) if (d.get('modifier') or 0) in (0, 3) else None


def where_of(T, ev, archives):
    """The archive row for an event's place (archives as load_archives returns it): the row whose match is the
    deepest level of the place hierarchy, the first such row in the file on a tie. None when no row matches, the event has no place or is private."""
    if not ev or ev.get('private') is True:
        return None
    chain = place_chain(T, ev.get('place'))
    folded = [fold(n) for n in chain]
    row = next((archives[n] for n in folded if n in archives), None)  # one lookup per level, the deepest first
    if row is None:
        return None
    y = exact_year(ev)
    url = row['url'].format(place=urllib.parse.quote(chain[0], safe=''), year=y or '')  # fields checked on load
    return {'archive': row['archive'], 'series': row['series'], 'url': url,
            'online': None if y is None else any(a <= y <= b for a, b in row['years']), 'access': row['access']}


def catalogue_dir():
    """The shipped catalogue: leads/archives/ in a clone, gwt_leads_archives/ beside the module in the wheel."""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ('archives', 'gwt_leads_archives'):
        if os.path.isdir(os.path.join(here, name)):
            return os.path.join(here, name)
    raise BadInput('the built-in archives catalogue is not installed next to %s' % __file__)


def catalogue_codes(directory):
    return sorted((f[:-4] for f in os.listdir(directory) if re.fullmatch(r'[A-Z]{2}\.tsv', f)),
                  key=lambda c: (c == 'XX', c))  # several-country rows last


def parse_catalogue_years(text):
    """parse_years plus an open end: '1871-' reads as 1871 to 9999, a source that still runs."""
    return parse_years(re.sub(r'(\d{1,4})\s*-\s*(?=,|$)', r'\1-9999', text.strip()))


def read_catalogue(path):
    """One catalogue TSV as a list of rows. Stricter than an --archives file, because we ship it: the header is
    CATALOGUE_COLUMNS in that order, every row has every cell, and every value is checked. BadInput on anything else."""
    try:
        with open(path, encoding='utf-8', newline='') as fh:
            lines = fh.read().split('\n')
    except (OSError, UnicodeDecodeError) as exc:
        raise BadInput('%s: %s' % (path, exc))
    if lines and lines[-1] == '':
        lines.pop()
    if not lines or tuple(lines[0].split('\t')) != CATALOGUE_COLUMNS:
        raise BadInput('%s: the header must be %s' % (path, '\\t'.join(CATALOGUE_COLUMNS)))
    rows = []
    for n, line in enumerate(lines[1:], 2):
        where = '%s line %d' % (path, n)
        cells = line.split('\t')
        if len(cells) != len(CATALOGUE_COLUMNS):
            raise BadInput('%s: %d cells, want %d' % (where, len(cells), len(CATALOGUE_COLUMNS)))
        row = dict(zip(CATALOGUE_COLUMNS, cells))
        if any(v != v.strip() for v in cells):
            raise BadInput('%s: a cell starts or ends with a space' % where)
        folded = [fold(m) if m != '*' else '*' for m in row['match'].split('|')]
        if not all(folded) or len(set(folded)) != len(folded):
            raise BadInput('%s: match %r has an empty or repeated name' % (where, row['match']))
        if row['match_level'] not in CATALOGUE_LEVELS or (row['match_level'] == 'world') != (folded == ['*']):
            raise BadInput('%s: match_level %r: one of %s, and world exactly when match is *' % (
                where, row['match_level'], ', '.join(CATALOGUE_LEVELS)))
        types = row['record_types'].split(',')
        if not row['archive'] or not set(types) <= set(RECORD_TYPES) or len(set(types)) != len(types):
            raise BadInput('%s: archive must not be empty and record_types must be some of %s' % (
                where, ', '.join(RECORD_TYPES)))
        if row['access'] not in CATALOGUE_ACCESS or row['robots'] not in CATALOGUE_ROBOTS:
            raise BadInput('%s: access %r or robots %r unknown' % (where, row['access'], row['robots']))
        if not re.fullmatch(r'(\d{4})?', row['civil_start']):
            raise BadInput('%s: civil_start %r is not a year' % (where, row['civil_start']))
        if row['tested']:
            try:
                datetime.date.fromisoformat(row['tested'])
            except ValueError:
                raise BadInput('%s: tested %r is not a date (YYYY-MM-DD)' % (where, row['tested']))
        try:
            years = parse_catalogue_years(row['years'])
            fields = [part[1:] for part in string.Formatter().parse(row['url']) if part[1] is not None]
        except (BadInput, ValueError) as exc:
            raise BadInput('%s: %s' % (where, exc))
        bad = [f for f, spec, conv in fields if spec or conv or f not in CATALOGUE_URL_FIELDS]
        if bad:
            raise BadInput('%s: url placeholder {%s} unknown; only %s' % (
                where, bad[0], ', '.join('{%s}' % f for f in CATALOGUE_URL_FIELDS)))
        if row['url'] and not row['url'].startswith(('https://', 'http://')):
            raise BadInput('%s: url must start with https:// or http://' % where)
        cover = [fold(c) for c in row['coverage_places'].split('|') if c.strip()]
        rows.append(dict(row, names=folded, record_types=frozenset(types), years_text=row['years'], years=years,
                         coverage=frozenset(cover),
                         line=n))
    return rows


def load_catalogue(spec, directory=None):
    """--archives builtin:es,fr (codes in any case, comma-separated) or builtin:all, as {folded name: [rows]} in file
    order, the files in the order asked. An unknown code is BadInput naming the codes there are. A region or place
    row of a country file carries home (its file's country names) and countries (every country name the catalogue
    knows), so catalogue_hints can refuse it for a same-name place in another country."""
    directory = directory or catalogue_dir()
    have = catalogue_codes(directory)
    asked = [c.strip().upper() for c in spec[len(BUILTIN):].split(',') if c.strip()]
    if asked == ['ALL']:
        asked = have
    unknown = [c for c in asked if c not in have]
    if not asked or unknown:
        raise BadInput('%s: no built-in catalogue %s; there are %s, and all' % (
            spec, ', '.join(unknown) or '(none named)', ', '.join(c.lower() for c in have)))
    files = {code: read_catalogue(os.path.join(directory, code + '.tsv')) for code in have}
    countries = frozenset(n for rows in files.values() for r in rows if r['match_level'] == 'country' for n in r['names'])
    index = {}
    for code in dict.fromkeys(asked):
        home = frozenset(n for r in files[code] if r['match_level'] == 'country' for n in r['names'])
        for row in files[code]:
            if code != 'XX' and row['match_level'] in ('region', 'place'):
                row.update(home=home, countries=countries)
            for name in row['names']:
                index.setdefault(name, []).append(row)
    return index


def given_surname(p):
    n = (p or {}).get('primary_name') or {}
    sn = next((s.get('surname', '') for s in n.get('surname_list', []) if s.get('surname')), '')
    return (n.get('first_name') or '').strip(), sn.strip()


def fill_url(template, values):
    """A catalogue url with its placeholders filled; every value is URL-encoded, so a space or an & in a name never
    breaks the query. A value the tree does not hold is empty."""
    def q(v):
        return urllib.parse.quote(str(v), safe='')
    return template.format(**{f: q(values.get(f, '')) for f in CATALOGUE_URL_FIELDS})


def catalogue_hints(T, ev, p, catalogue):
    """(hints, outside) for the record a gap asks for, from load_catalogue's index. A row fits when one of its names
    is a level of the event's place (deepest level first, then the world rows), its record_types hold the record
    (birth for a birth or baptism, death for a death or burial), its years hold the event's year and its
    coverage_places, when it has any, include a level of the place. Rows of one level go by access (online first,
    in-person last). A region or place row of a country file fits only when the place names that country, or names
    no country the catalogue knows: Paris, Texas never gets the Archives de Paris. A row that matches a named level
    of the place and the record but not the year or the places is listed in outside with why: searching there cannot
    find this record. World rows (match *) are never listed outside; they are not about this place."""
    if not ev or ev.get('private', False) is True:
        return [], []
    want = 'birth' if etype(ev) in BIRTH_LIKE else 'death' if etype(ev) in DEATH_LIKE else None
    chain = place_chain(T, ev.get('place'))
    if want is None or not chain:
        return [], []
    levels = [fold(n) for n in chain]
    named = set(levels)
    rows, seen = [], set()
    for name in levels + ['*']:
        for row in sorted(catalogue.get(name, ()), key=lambda r: CATALOGUE_ACCESS.index(r['access'])):
            if row.get('home') is not None and named & row['countries'] and not named & row['home']:
                continue  # a same-name place in another country
            if id(row) not in seen:
                seen.add(id(row))
                rows.append(row)
    y = exact_year(ev)
    given, surname = given_surname(p)
    place = (T.get('places', {}).get(ev.get('place')) or {})
    values = {'place': chain[0], 'year': y or '', 'surname': surname, 'given': given, 'code': place.get('code') or '',
              'year_from': y - YEAR_WINDOW if y else '', 'year_to': y + YEAR_WINDOW if y else ''}
    hints, outside = [], []
    for row in rows:
        if want not in row['record_types']:
            continue
        world = row['match_level'] == 'world'
        if row['coverage'] and not row['coverage'] & set(levels):
            outside.append({'archive': row['archive'], 'why': 'covers only ' + row['coverage_places'].replace('|', ', ')})
        elif y is not None and row['years'] and not any(lo <= y <= hi for lo, hi in row['years']):
            if not world:  # Yad Vashem's years say nothing about an 1890 death
                outside.append({'archive': row['archive'], 'why': 'covers %s, not %d' % (row['years_text'], y)})
        else:
            hints.append({'archive': row['archive'], 'series': row['series'], 'url': fill_url(row['url'], values),
                          'online': False if row['access'] != 'online' else None if y is None else True,
                          'access': row['access']})
    return hints, outside


def label(p):
    n = p.get('primary_name') or {}
    sn = ' '.join(s.get('surname', '') for s in n.get('surname_list', []) if s.get('surname'))
    return ' '.join(x for x in (n.get('first_name', ''), sn) if x) or p.get('gramps_id', '')


def weak_name(p):
    """A name too thin to search an outside site with: no given name, no surname, or none at all."""
    n = p.get('primary_name') or {}
    given = (n.get('first_name') or '').strip()
    surnames = [s.get('surname', '').strip() for s in n.get('surname_list', []) if s.get('surname', '').strip()]
    return not given or not surnames


def is_birth_relation(value):
    return text_of(value) in ('', 'Birth', 'Unknown')


def parents(T, p):
    """Birth parents only: an adoptive, step or foster family does not continue the blood line."""
    out = []
    for fh in p.get('parent_family_list', []):
        f = T['families'].get(fh) or {}
        ref = next((c for c in f.get('child_ref_list', []) if c.get('ref') == p['handle']), {})
        for h, rel in ((f.get('father_handle'), ref.get('frel')), (f.get('mother_handle'), ref.get('mrel'))):
            if h in T['people'] and is_birth_relation(rel):
                out.append(h)
    return out


def generations(T, root):
    """handle -> generation (0 = root) for the root and every ancestor, breadth first."""
    gen, queue = {root: 0}, collections.deque([root])
    while queue:
        h = queue.popleft()
        for par in parents(T, T['people'][h]):
            if par not in gen:
                gen[par] = gen[h] + 1
                queue.append(par)
    return gen


def own_events(T, p):
    """The person's own events, in ref order: refs with role Primary (a missing role reads as Primary).
    A godparent's or witness's ref to someone else's baptism is not their own."""
    return [T['events'][r['ref']] for r in p.get('event_ref_list', [])
            if r.get('ref') in T['events'] and text_of(r.get('role')) in ('', 'Primary')]


def vital(T, p, types, index_field):
    """The event that stands for a person's birth (or death): the one the person points at with
    birth_ref_index (or death_ref_index) when that ref is Primary, else the first own event of the first type in
    `types` that the person has."""
    refs = p.get('event_ref_list', [])
    i = p.get(index_field)
    if isinstance(i, int) and 0 <= i < len(refs):
        r = refs[i]
        e = T['events'].get(r.get('ref'))
        if e and text_of(r.get('role')) in ('', 'Primary') and etype(e) in types:
            return e
    evs = own_events(T, p)
    for t in types:
        for e in evs:
            if etype(e) == t:
                return e
    return None


def birth(T, p):
    return vital(T, p, BIRTH_LIKE, 'birth_ref_index')


def death(T, p):
    return vital(T, p, DEATH_LIKE, 'death_ref_index')


def birth_dated(T, p):
    """The birth event when it has a year, else the first own Baptism or Christening that has one."""
    b = birth(T, p)
    if b and year(b):
        return b
    return next((e for e in own_events(T, p) if etype(e) in BIRTH_LIKE[1:] and year(e)), b)


def not_living(T, living_after):
    """Handles of the people the dates prove were born in living_after or earlier: their own birth or baptism,
    their death, or the marriage of a family they head ends by then, or so does that of anyone descending from
    them. A descendant born in 1900 means every ancestor was born before 1900, so the direct line above any
    dated person is never held back."""
    def by_cutoff(e):
        y = latest_year(e)
        return y is not None and y <= living_after
    seeds = {h for h, p in T['people'].items()
             if any(by_cutoff(e) for e in own_events(T, p) if etype(e) in BIRTH_LIKE) or by_cutoff(birth(T, p))
             or by_cutoff(death(T, p))}
    for f in T['families'].values():
        if any(by_cutoff(T['events'].get(r.get('ref'))) and etype(T['events'][r['ref']]) in MARRIAGE_LIKE
               for r in f.get('event_ref_list', []) if r.get('ref') in T['events']):
            seeds.update(h for h in (f.get('father_handle'), f.get('mother_handle')) if h in T['people'])
    old, queue = set(seeds), collections.deque(seeds)
    while queue:
        p = T['people'][queue.popleft()]
        for fh in p.get('parent_family_list', []):
            f = T['families'].get(fh) or {}
            for h in (f.get('father_handle'), f.get('mother_handle')):
                if h in T['people'] and h not in old:
                    old.add(h)
                    queue.append(h)
    return old


def held_back(T, p, living_after, old=None):
    """Why a person stays out of every output, or '': marked private, or may be living: no death, burial or
    cremation, and nothing in `old` (see not_living) proves a birth in living_after or earlier."""
    if p.get('private') is True:
        return 'private'
    if death(T, p):
        return ''
    if old is None:
        old = not_living(T, living_after)
    return '' if p['handle'] in old else 'living'


def facts(T, p):
    """What the tree knows about a person's birth and death, as search filters: {'by': '~1860', 'bp': 'Parish,
    Town', 'dy': ...}. A baptism or burial stand-in is keyed 'bap' or 'bur'. Private events say nothing."""
    out = {}
    for e in (birth_dated(T, p), death(T, p)):
        if not e or e.get('private') is True:
            continue
        k = FACT_KEYS[etype(e)]
        if year_text(e):
            out[k + 'y'] = year_text(e)
        place = place_name(T, e.get('place'))
        if place:
            out[k + 'p'] = place
    return out


def gap_event(T, p, gap):
    """The event whose record a gap asks for: the birth (or the baptism that dates it) for a branch end, a birth
    gap or an unsupported link, the death (or burial) for a death gap, None for a marriage gap."""
    if gap == 'branch-end' or gap.startswith(('birth-', 'link-')):
        return birth_dated(T, p)
    if gap.startswith('death-'):
        return death(T, p)
    return None


def leads(T, root_gid=None, kinds=DEFAULT_KINDS, living_after=None, hold=True, links=None, archives=None,
          catalogue=None):
    """(rows, held): one row per gap, highest priority first, and {gramps_id: why} for the people held back
    (none when hold is False). With archives (load_archives), a gap whose place matches a row gets 'where'. With
    catalogue (load_catalogue), the first fitting row is 'where', the others 'also', and the rows that match the place
    but not the year or the places are 'outside' (catalogue_hints)."""
    living_after = default_living_after() if living_after is None else living_after
    gen = {}
    if root_gid:
        root = next((h for h, p in T['people'].items() if p.get('gramps_id') == root_gid), None)
        if root is None:
            raise BadInput('no person %s in the dump' % root_gid)
        gen = generations(T, root)
    out, held = [], {}
    old = not_living(T, living_after)

    def add(p, kind, gap, what):
        g = gen.get(p['handle'])
        prio = max(40, 100 - g) if g is not None else 10
        out.append({'person': p.get('gramps_id', p['handle']), 'label': label(p), 'kind': kind, 'gap': gap,
                    'what': what, 'priority': prio, 'generation': g, 'weak': weak_name(p), 'facts': facts(T, p)})
        where = where_of(T, gap_event(T, p, gap), archives) if archives else None
        if where:
            out[-1]['where'] = where
        if catalogue:
            hints, outside = catalogue_hints(T, gap_event(T, p, gap), p, catalogue)
            if hints:
                out[-1]['where'] = hints[0]
            if hints[1:]:
                out[-1]['also'] = hints[1:]
            if outside:
                out[-1]['outside'] = outside

    for p in T['people'].values():
        why = held_back(T, p, living_after, old) if hold else ''
        if why:
            held[p.get('gramps_id', p['handle'])] = why
            continue
        b, d, by = birth(T, p), death(T, p), birth_dated(T, p)
        if 'branch-end' in kinds and not parents(T, p):
            add(p, 'branch-end', 'branch-end', 'no parents recorded')
        if 'missing-vital' in kinds:
            if not b:
                add(p, 'missing-vital', 'birth-missing', 'no birth or baptism event')
            if not d and p['handle'] in old:
                shown = by and year(by) and by.get('private') is not True
                add(p, 'missing-vital', 'death-missing', 'no death or burial event' +
                    (' (%s %d)' % (etype(by).lower(), year(by)) if shown else ''))
            for code, e in (('birth', b), ('death', d)):
                if not e:
                    continue
                if date_gap(e):
                    gap = date_gap(e)
                    if e.get('private') is True and gap != 'has no date':
                        gap = 'has date text without an interpretable year'  # the text of a private date stays out
                    add(p, 'missing-vital', code + '-date', '%s %s' % (etype(e).lower(), gap))
                if not e.get('place'):
                    add(p, 'missing-vital', code + '-place', '%s has no place' % etype(e).lower())
        if 'uncited' in kinds:
            for code, e in (('birth', b), ('death', d)):
                if e and not e.get('citation_list'):
                    add(p, 'uncited', code + '-uncited', '%s has no citation' % etype(e).lower())
    if 'missing-vital' in kinds:
        for f in T['families'].values():
            if f.get('child_ref_list') and not any(
                    etype(T['events'].get(r.get('ref'), {})) in MARRIAGE_LIKE for r in f.get('event_ref_list', [])):
                for h in (f.get('father_handle'), f.get('mother_handle')):
                    p = T['people'].get(h)
                    if p and not (hold and held_back(T, p, living_after, old)):
                        add(p, 'missing-vital', 'marriage-missing-%s' % f.get('gramps_id', f['handle']),
                            'family %s has children but no marriage event' % f.get('gramps_id', ''))
                        break
    if 'unsupported-link' in kinds:
        by_gid = {p.get('gramps_id'): p for p in T['people'].values()}
        fam_by_gid = {f.get('gramps_id'): f for f in T['families'].values()}
        stale = 0
        for row in (links or {}).get('rows', []):
            if not (isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ('family', 'child', 'level'))):
                raise BadInput('not a gwt-check links report: every row needs a family, a child and a level')
            if row['level'] not in LINK_GAPS and row['level'] not in LINK_NOT_GAPS:
                raise BadInput('links report: unknown level %r (is it from a newer gwt-check?)' % row['level'])
            p, f = by_gid.get(row['child']), fam_by_gid.get(row['family'])
            ref = next((c for c in (f or {}).get('child_ref_list', []) if p and c.get('ref') == p['handle']), None)
            if ref is None:
                stale += 1
                continue
            gap = LINK_GAPS.get(row['level'])
            if gap is None or not (is_birth_relation(ref.get('frel')) and is_birth_relation(ref.get('mrel'))):
                continue  # supported, listed by another kind, or not a birth link a birth record could prove
            if row['level'] == 'a record names one' and \
                    sum(f.get(k) in T['people'] for k in ('father_handle', 'mother_handle')) < 2:
                continue  # the one parent the family has is named
            why = held_back(T, p, living_after, old) if hold else ''
            if why:
                held[p.get('gramps_id', p['handle'])] = why
                continue
            flags = [x for x in row.get('flags') or [] if isinstance(x, str)]
            add(p, 'unsupported-link', 'link-%s' % row['family'],
                'family %s: %s; transcribe the parents from the cited record into its citation, or find a record '
                'that names both (words only: the link is not shown wrong)%s'
                % (row['family'], gap, ' [read first: %s]' % '; '.join(flags) if flags else ''))
        if stale:
            raise BadInput('%d rows of the links report name a child who is not in that family in this dump: the '
                           'report is from another tree or an older dump, so run gwt-check links on this one' % stale)
    out.sort(key=lambda r: (-r['priority'], id_order(r['person']), r['kind'], r['gap']))
    return out, held


def id_order(gid):
    """I2 before I10: the letters, then the number, then the ID itself (I01 before I1, whatever the input order)."""
    m = re.fullmatch(r'([A-Za-z]*)(\d+)', gid or '')
    return (m.group(1), int(m.group(2)), gid) if m else (gid, -1, gid)


def task_of(row):
    return 'verify' if row['kind'] in ('uncited', 'unsupported-link') else 'search'


def query_of(row):
    """What a lane types into a search: the name, the birth year and the birth place when the tree knows them."""
    f = row.get('facts') or {}
    y = f.get('by') or f.get('bapy') or ''
    place = (f.get('bp') or f.get('bapp') or '').split(',')[0]
    return ' '.join(x for x in (row['label'], y.lstrip('<>~'), place) if x)


def fact_note(f):
    """facts as 'by=~1860 bp=San_Juan,Villa': spaces as _, and %, |, ;, = and _ escaped so the note stays parseable."""
    def esc(v):
        return re.sub(r'[%|;=_\s]', lambda m: '_' if m.group().isspace() else '%%%02X' % ord(m.group()), str(v))
    return ' '.join('%s=%s' % (k, esc(v)) for k, v in sorted(f.items()))


def where_note(w):
    """A where object in fact_note's escaping; online as true or false, and empty values left out."""
    shown = {k: ('true' if v else 'false') if isinstance(v, bool) else v for k, v in w.items()}
    return fact_note({k: v for k, v in shown.items() if v not in (None, '')})


def group_by_person(rows):
    """One task per person and task kind: the gaps of one person are one search, not one search per gap.
    Priority is per person, so every row of a group carries the same one."""
    groups = {}
    for r in rows:
        g = groups.setdefault((r['person'], task_of(r)), dict(r, task=task_of(r), gaps=[]))
        g['gaps'].append(r)
    return list(groups.values())


def queue_task(r, source=''):
    """One JSON object for `gwt-queue add --jsonl`. The dedupe key is person, task kind and source (plus the gap
    code with --per-gap), never the name, so a corrected name refreshes the open task instead of adding one."""
    gaps = r.get('gaps') or [r]
    task = task_of(r)
    note = '; '.join('%s: %s' % (g['gap'], g['what']) for g in gaps)
    wheres = []
    for g in gaps:
        if g.get('where') and g['where'] not in wheres:
            wheres.append(g['where'])
        for w in g.get('also', [])[:NOTE_HINTS - 1]:  # the catalogue's next best rows, after its first
            if w not in wheres:
                wheres.append(w)
    if wheres:
        note = 'where: %s | %s' % ('; '.join(where_note(w) for w in wheres), note)
    if r.get('facts'):
        note = 'facts: %s | %s' % (fact_note(r['facts']), note)
    dedupe_key = ':'.join([r['person'].lower(), task, source.lower()] + ([] if 'gaps' in r else [r['gap']]))
    return {'person': r['person'], 'label': r['label'], 'kind': task, 'source': source, 'query': query_of(r),
            'priority': r['priority'], 'note': note, 'dedupe_key': dedupe_key}


def tsv_cell(value):
    return ' '.join(str(value).replace('\t', ' ').splitlines()).strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a 3xx is the answer itself: the probe never follows it
        return None


def http_fetch(method, url, timeout=30):
    """(status, body text) for one request with the probe's User-Agent; status None and the reason as text when no
    answer came. Redirects are not followed, except for a robots.txt, which RFC 9309 says to follow (many hosts
    move theirs to a CDN or to https)."""
    req = urllib.request.Request(url, method=method, headers={'User-Agent': PROBE_AGENT})
    follow = url.endswith('/robots.txt')
    try:
        with urllib.request.build_opener(*(() if follow else (_NoRedirect,))).open(req, timeout=timeout) as r:
            return r.status, r.read(200000).decode('utf-8', 'replace') if method == 'GET' else ''
    except urllib.error.HTTPError as exc:
        return exc.code, ''
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, str(getattr(exc, 'reason', exc))


def probe(directory, codes, fetch=http_fetch, sleep=time.sleep, delay=2.0, today=None, out=sys.stdout):
    """Check every url of the catalogue files named by codes, filled with invented values (PROBE_VALUES): robots.txt
    first (one fetch per host; a 4xx robots.txt allows all, a 5xx or no answer forbids all, as RFC 9309 says), then
    HEAD (GET when HEAD is refused with 405 or 501), with delay seconds before each request. A 2xx or 3xx answer
    writes today's date into tested; nothing else in the file changes. Returns how many urls failed or were
    forbidden by robots.txt."""
    today = today or datetime.date.today().isoformat()
    robots, failed = {}, 0
    col = CATALOGUE_COLUMNS.index('tested')
    for code in codes:
        path = os.path.join(directory, code + '.tsv')
        read_catalogue(path)  # a bad file stops here, before any request
        with open(path, encoding='utf-8', newline='') as fh:
            lines = fh.read().split('\n')
        changed = False
        for n, line in enumerate(lines[1:], 1):
            if not line:
                continue
            cells = line.split('\t')
            row = dict(zip(CATALOGUE_COLUMNS, cells))
            if not row['url']:
                continue
            url = fill_url(row['url'], PROBE_VALUES)
            u = urllib.parse.urlsplit(url)
            host = '%s://%s' % (u.scheme, u.netloc)
            if host not in robots:
                sleep(delay)
                status, text = fetch('GET', host + '/robots.txt')
                rp = urllib.robotparser.RobotFileParser()
                if status is not None and 200 <= status < 300:
                    rp.parse(text.splitlines())
                elif status is not None and 400 <= status < 500:
                    rp.allow_all = True
                else:
                    rp.disallow_all = True
                robots[host] = rp
            if not robots[host].can_fetch(PROBE_AGENT, url):
                failed += 1
                print('robots\t-\t%s\t%s\t%s' % (code, row['archive'], url), file=out)
                continue
            sleep(delay)
            status, reason = fetch('HEAD', url)
            if status in (405, 501):
                sleep(delay)
                status, reason = fetch('GET', url)
            ok = status is not None and 200 <= status < 400
            if ok and cells[col] != today:
                cells[col] = today
                lines[n] = '\t'.join(cells)
                changed = True
            failed += not ok
            print('%s\t%s\t%s\t%s\t%s' % ('ok' if ok else 'FAIL', status if status is not None else reason[:80],
                                          code, row['archive'], url), file=out, flush=True)
        if changed:
            with open(path, 'w', encoding='utf-8', newline='') as fh:
                fh.write('\n'.join(lines))
    return failed


def probe_main(argv):
    p = argparse.ArgumentParser(prog='gwt-leads archives probe',
                                description='Check the catalogue urls live and date the ones that answer. For '
                                            'maintainers, from a clone; never run in CI.')
    p.add_argument('--country', action='append', default=[], metavar='CC',
                   help='only these catalogue files (comma-separated or repeated); default every file')
    p.add_argument('--dir', help='the catalogue folder (default: the one gwt-leads ships)')
    p.add_argument('--delay', type=float, default=2.0, help='seconds before each request (default 2, never less than 1)')
    args = p.parse_args(argv)
    if args.delay < 1:
        p.error('--delay must be at least 1 second')
    try:
        directory = args.dir or catalogue_dir()
        have = catalogue_codes(directory)
        codes = [c.strip().upper() for a in args.country for c in a.split(',') if c.strip()] or have
        unknown = [c for c in codes if c not in have]
        if unknown:
            raise BadInput('no catalogue file %s in %s' % (', '.join(unknown), directory))
        failed = probe(directory, codes, delay=args.delay)
    except BadInput as exc:
        print('gwt-leads: %s' % exc, file=sys.stderr)
        return 2
    print('gwt-leads: %d url(s) did not answer 2xx or 3xx or are forbidden by robots.txt' % failed, file=sys.stderr)
    return 1 if failed else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:2] == ['archives', 'probe']:
        return probe_main(argv[2:])
    p = argparse.ArgumentParser(prog='gwt-leads', description=__doc__.split('\n')[0])
    p.add_argument('dump')
    p.add_argument('--root', help='Gramps id of the person whose ancestors rank first')
    p.add_argument('--kinds', default=','.join(DEFAULT_KINDS))
    p.add_argument('--links', metavar='FILE',
                   help='the JSON `gwt-check links --json` wrote from this same dump: adds the unsupported-link kind')
    p.add_argument('--archives', metavar='FILE',
                   help='TSV with match, archive, series, years, url (and optionally access): each gap whose place '
                        'matches a row gets where its record is kept and whether its year is online. '
                        'builtin:es,fr (country codes) or builtin:all reads the catalogue shipped with gwt-leads')
    p.add_argument('--limit', type=int, default=0, help='keep the N highest-priority rows (tasks, in queue format)')
    p.add_argument('--format', choices=('queue', 'tsv', 'json'), default='tsv')
    p.add_argument('--living-after', type=int, default=default_living_after(), metavar='YEAR',
                   help='with no death, burial or cremation, someone whose dates (or a descendant\'s) do not prove a '
                        'birth in YEAR or earlier may be living and is held back from every format '
                        '(default: this year minus 100, %(default)s)')
    p.add_argument('--include-living', action='store_true',
                   help='do not hold back the possibly living and the private: only for a local, private review')
    p.add_argument('--source', default='', help='queue format: the source of every task, part of its dedupe key')
    p.add_argument('--per-gap', action='store_true', help='queue format: one task per gap instead of one per person')
    p.add_argument('--skip-weak', action='store_true',
                   help='queue format: leave out people with no given name or no surname, unless they are '
                        'the root or one of its ancestors; their ids go to stderr')
    args = p.parse_args(argv)
    if args.format != 'queue':
        for flag, on in (('--source', args.source), ('--per-gap', args.per_gap), ('--skip-weak', args.skip_weak)):
            if on:
                p.error('%s only applies to --format queue' % flag)
    kinds = tuple(k for k in args.kinds.split(',') if k)
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        p.error('unknown kind: %s' % ', '.join(bad))
    if args.links and 'unsupported-link' not in kinds:
        kinds += ('unsupported-link',)  # --links can only mean this kind
    if 'unsupported-link' in kinds and not args.links:
        p.error('the unsupported-link kind needs --links FILE, the JSON `gwt-check links --json` writes')
    links = None
    if args.links:
        try:
            with open(args.links, encoding='utf-8') as fh:
                links = json.load(fh)
            if not isinstance(links, dict) or not isinstance(links.get('rows'), list):
                raise ValueError('not a gwt-check links report')
        except (OSError, ValueError) as exc:
            print('gwt-leads: %s: %s' % (args.links, exc), file=sys.stderr)
            return 2
    try:
        builtin = (args.archives or '').startswith(BUILTIN)
        archives = load_archives(args.archives) if args.archives and not builtin else None
        catalogue = load_catalogue(args.archives) if builtin else None
        rows, held = leads(load(args.dump), args.root, kinds, args.living_after, hold=not args.include_living,
                           links=links, archives=archives, catalogue=catalogue)
    except BadInput as exc:
        print('gwt-leads: %s' % exc, file=sys.stderr)
        return 2
    if held:
        n_private = sum(1 for why in held.values() if why == 'private')
        print('gwt-leads: held back %d people (%d marked private, %d who may be living: born after %d or in an unknown '
              'year, no death): %s' % (len(held), n_private, len(held) - n_private, args.living_after,
                                       ' '.join(sorted(held, key=id_order))), file=sys.stderr)
    if args.format == 'queue':
        if args.skip_weak:
            weak = sorted({r['person'] for r in rows if r['weak'] and r['generation'] is None}, key=id_order)
            rows = [r for r in rows if not (r['weak'] and r['generation'] is None)]
            if weak:
                print('gwt-leads: skipped %d people with no given name or no surname: %s' % (len(weak), ' '.join(weak)),
                      file=sys.stderr)
        if not args.per_gap:
            rows = group_by_person(rows)
    if args.limit:
        rows = rows[:args.limit]
    for r in rows:
        if args.format == 'queue':
            print(json.dumps(queue_task(r, args.source), ensure_ascii=False))
        elif args.format == 'json':
            print(json.dumps(r, ensure_ascii=False))
        else:
            print('\t'.join(tsv_cell(r[c]) for c in ('priority', 'person', 'label', 'kind', 'what')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
