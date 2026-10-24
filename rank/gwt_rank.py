#!/usr/bin/env python3
"""gwt-rank: order the rows of a record search by how well they fit a person in the tree.

A search by name returns thousands of rows, and a lane that stops at "N results" has found nothing.
This reads the rows and puts the ones that agree with what the tree already knows first. It never
hides a row: every row is printed with a tier, so a page with no strong row is still a list of leads.

Every source is first turned into one normalized candidate record per row (see record() and
docs/rank.md), and the scorer reads only that record. Inputs: the FamilySearch search JSON (GEDCOM X,
the body `gwt-fscache` stores for a `/service/search/hr/` URL), already normalized records
(`{"candidates": [...]}`, what another site's normalizer writes), or, as the fallback, the visible
text of a results page with Spanish or English labels.

  gwt-rank FILE... --given "Maria del Carmen" --surname Perez [--surname Lopez]
           [--facts "by=1901 bp=Madrid dy=1970 dp=Murcia"] [--spouse "Juan Ruiz"] [--format tsv|json]

Tiers, best first: strong (the principal, name matches, two independent agreements beyond the name),
possible (name matches and one agreement), weak (everything else), conflict (a year the tree
contradicts). A strong row is a lead for the verifier, never an identification.

Exit 0 when a row is strong, 1 when rows exist and none is strong (ledger outcome `partial`, never
`none`), 2 when there is nothing to rank (not a results page, not loaded, or 0 rows).
"""
import argparse
import difflib
import json
import re
import sys
import unicodedata

YEAR = re.compile(r'(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)')
GX = 'http://gedcomx.org/'
FS = 'http://familysearch.org/types/facts/'
# best evidence first: a baptism or burial stands in when the record has no birth or death
BIRTH_TYPES = (GX + 'Birth', GX + 'Christening', GX + 'Baptism', FS + 'BirthRegistration')
DEATH_TYPES = (GX + 'Death', GX + 'Burial', GX + 'Cremation', FS + 'DeathRegistration')
NAME_PARTICLES = {'de', 'del', 'la', 'las', 'los', 'y', 'i', 'e', 'da', 'das', 'do', 'dos', 'di', 'van', 'von', 'der'}
PLACE_GENERIC = {'san', 'santa', 'santo', 'saint', 'sainte', 'st', 'ste', 'la', 'le', 'les', 'el', 'los', 'las', 'new',
                 'de', 'del', 'du', 'des', 'sur', 'sous', 'en', 'y', 'and', 'of', 'the', 'villa', 'puerto', 'port',
                 'mont', 'monte', 'val', 'ville'}
TIERS = ('strong', 'possible', 'weak', 'conflict')
NAME_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv'}

# Page-text labels, compared after norm(): English and Spanish FamilySearch, and the generic ones other sites use.
PRINCIPAL = {'principal', 'persona principal', 'self'}
ROLES = PRINCIPAL | {'father', 'mother', 'spouse', 'husband', 'wife', 'son', 'daughter', 'child', 'groom', 'bride',
                     'deceased', 'head', 'other', 'padre', 'madre', 'conyuge', 'esposo', 'esposa', 'marido', 'mujer',
                     'hijo', 'hija', 'novio', 'novia', 'difunto', 'difunta', 'fallecido', 'fallecida', 'otro', 'otra',
                     'cabeza de familia', 'jefe de familia'}
SPOUSE_LABEL = r'(spouses?|husband|wife|c[oó]nyuges?|esposo|esposa|marido)'
BIRTH_TEXT = re.compile(r'^(birth|baptism|christening|nacimiento|bautismo|bautizo)\b', re.I)
DEATH_TEXT = re.compile(r'^(death|burial|defunci[oó]n|fallecimiento|muerte|entierro|sepultura)\b', re.I)
NOT_A_PLACE = re.compile(r'^(birth|baptism|christening|death|burial|marriage|residence|census|immigration|emigration|'
                         r'parents|spouses?|more|nacimiento|bautismo|bautizo|defunci[oó]n|fallecimiento|muerte|entierro|'
                         r'sepultura|matrimonio|residencia|censo|inmigraci[oó]n|emigraci[oó]n|padres|c[oó]nyuges?|'
                         r'esposo|esposa|m[aá]s)\b', re.I)


def norm(text):
    t = unicodedata.normalize('NFKD', text or '')
    t = ''.join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9 ]+', ' ', t).split()


def years(text):
    """(low, high) of the years in a date, or None. A range '+1860/+1865' gives (1860, 1865)."""
    ys = [int(y) for y in YEAR.findall(text or '')]
    return (min(ys), max(ys)) if ys else None


def record(source, rid, name, role, principal, given=None, surnames=None):
    """The normalized candidate record, the only thing the scorer reads. given and surnames are None
    when the source gives only the full name. Each event is {'year', 'year_to', 'place'}."""
    return {'source': source, 'id': rid, 'name': name, 'role': role, 'principal': principal,
            'given': given, 'surnames': surnames, 'events': {}, 'relatives': []}


def _set_event(r, kind, yr, place):
    if yr or place:
        r['events'][kind] = {'year': yr and yr[0], 'year_to': yr and yr[1], 'place': place or ''}


def _year_range(event):
    if not event or event.get('year') is None:
        return None
    return int(event['year']), int(event.get('year_to') or event['year'])


# --- GEDCOM X search JSON -------------------------------------------------------------------------

def _name(person):
    for n in person.get('names') or []:
        for form in n.get('nameForms') or []:
            given = [p.get('value', '') for p in form.get('parts') or [] if p.get('type') == GX + 'Given']
            sur = [p.get('value', '') for p in form.get('parts') or [] if p.get('type') == GX + 'Surname']
            full = form.get('fullText') or ' '.join(given + sur)
            if full:
                return full, (' '.join(given) if given or sur else None), (sur if given or sur else None)
    return (person.get('display') or {}).get('name', ''), None, None


def _place(fact):
    pl = fact.get('place') or {}
    return ', '.join([pl.get('original', '')] + [v.get('value', '') for v in pl.get('normalized') or []]).strip(', ')


def _event(facts, types):
    """(year range, place) from the best fact of these types; year and place may come from different facts."""
    yr = place = None
    for t in types:
        for f in facts:
            if f.get('type') != t:
                continue
            d = f.get('date') or {}
            yr = yr or years(d.get('formal') or '') or years(d.get('original') or '')
            place = place or _place(f)
    return yr, place or ''


def gedcomx_rows(doc):
    """One record per entry: the persona the search matched, with only its own facts."""
    rows = []
    for e in doc.get('entries') or []:
        g = (e.get('content') or {}).get('gedcomx') or {}
        persons = g.get('persons') or []
        by_ref = {'#' + p.get('id', ''): p for p in persons}
        eid = e.get('id', '')
        hit = [p for p in persons if eid and any(i.rstrip('/').endswith((':' + eid, '/' + eid))
                                                 for i in (p.get('identifiers') or {}).get(GX + 'Persistent', []))]
        for p in hit or [p for p in persons if p.get('principal')]:
            full, given, sur = _name(p)
            role = (p.get('display') or {}).get('role') or ('Principal' if p.get('principal') else '')
            c = record('familysearch', eid, full, role, bool(p.get('principal')), given, sur)
            facts = p.get('facts') or []
            _set_event(c, 'birth', *_event(facts, BIRTH_TYPES))
            _set_event(c, 'death', *_event(facts, DEATH_TYPES))
            ref = '#' + p.get('id', '')
            for r in g.get('relationships') or []:
                ends = [(r.get('person1') or {}).get('resource'), (r.get('person2') or {}).get('resource')]
                if ref not in ends:
                    continue
                other = by_ref.get(ends[1] if ends[0] == ref else ends[0])
                if not other:
                    continue
                if r.get('type') == GX + 'Couple':
                    rel = 'spouse'
                elif r.get('type') == GX + 'ParentChild' and ends[1] == ref:   # person1 is the parent
                    rel = {GX + 'Male': 'father', GX + 'Female': 'mother'}.get((other.get('gender') or {}).get('type'), 'parent')
                elif r.get('type') == GX + 'ParentChild':
                    rel = 'child'
                else:
                    rel = 'other'
                c['relatives'].append({'role': rel, 'name': _name(other)[0]})
            rows.append(c)
    return rows


def _bad_record(r):
    """Why a normalized record does not have the documented shape, or '' when it does."""
    def strs(v):
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if not isinstance(r, dict) or not isinstance(r.get('name'), str):
        return 'needs a name string'
    if r.get('given') is not None and not isinstance(r['given'], str):
        return 'given must be a string or null'
    if r.get('surnames') is not None and not strs(r['surnames']):
        return 'surnames must be a list of strings or null'
    if not isinstance(r.get('events', {}), dict):
        return 'events must be an object'
    for kind, e in (r.get('events') or {}).items():
        if not isinstance(e, dict) or not isinstance(e.get('place', ''), str) or \
                any(e.get(k) is not None and (isinstance(e[k], bool) or not isinstance(e[k], int)) for k in ('year', 'year_to')):
            return 'events.%s must be {year, year_to, place}' % kind
    rel = r.get('relatives') or []
    if not isinstance(rel, list) or not all(isinstance(x, dict) and isinstance(x.get('name', ''), str)
                                            and isinstance(x.get('role', ''), str) for x in rel):
        return 'relatives must be a list of {role, name}'
    return ''


def normalized_rows(doc):
    """Records another normalizer already wrote, checked for the fields the scorer reads."""
    rows = []
    for n, r in enumerate(doc.get('candidates') or []):
        bad = _bad_record(r)
        if bad:
            raise ValueError('candidate %d: %s' % (n + 1, bad))
        c = record(r.get('source', ''), str(r.get('id', '')), r['name'], r.get('role', ''), bool(r.get('principal')),
                   r.get('given'), r.get('surnames'))
        for kind in ('birth', 'death'):
            e = (r.get('events') or {}).get(kind)
            if e:
                _set_event(c, kind, _year_range(e), e.get('place'))
        c['relatives'] = [{'role': x.get('role', ''), 'name': x.get('name', '')} for x in r.get('relatives') or []]
        rows.append(c)
    return rows


# --- page text, the fallback ------------------------------------------------------------------------

def text_rows(text):
    """Rows from the visible text of a results page: a name line, then a role line, starts a row.

    When the page puts a blank line between the name and the role (FamilySearch does), only that
    shape starts a row, so a 'Spouse' label inside a row is read as a field, not as a new row."""
    lines, blank = [], False
    for raw in text.splitlines():
        if raw.strip():
            lines.append((raw.strip(), blank))
            blank = False
        else:
            blank = True
    key = [' '.join(norm(l)) for l, _ in lines]
    starts = [i for i in range(len(lines) - 1) if key[i + 1] in ROLES and key[i] not in ROLES
              and not BIRTH_TEXT.match(lines[i][0]) and not DEATH_TEXT.match(lines[i][0])]
    if any(lines[i + 1][1] for i in starts):
        starts = [i for i in starts if lines[i + 1][1]]
    rows = []
    for n, i in enumerate(starts):
        body = [l for l, _ in lines[i + 2:(starts[n + 1] if n + 1 < len(starts) else len(lines))]]
        c = record('text', '', lines[i][0], lines[i + 1][0], key[i + 1] in PRINCIPAL)
        ev = {}
        j = 1   # body[0] is the collection title: 'Baptism Register 1850-1900' is not an event
        while j < len(body):
            line = body[j]
            kind = 'b' if BIRTH_TEXT.match(line) else 'd' if DEATH_TEXT.match(line) else None
            if kind:
                # the year sits on the event line, or on the next line when the date is on its own line
                k = j if years(line) else (j + 1 if j + 1 < len(body) and years(body[j + 1]) else None)
                if k is not None and kind not in ev:
                    place = body[k + 1] if k + 1 < len(body) and not NOT_A_PLACE.match(body[k + 1]) else ''
                    ev[kind] = (years(body[k]), place)
                    j = k
            m = re.match(SPOUSE_LABEL + r'\s*:?\s*(.*)$', line, re.I)
            if m:
                value = m.group(2) or (body[j + 1] if j + 1 < len(body) else '')
                c['relatives'] += [{'role': 'spouse', 'name': s} for s in re.split(r'\s*[,;]\s*|\s+(?:and|y)\s+', value) if s.strip()]
                j += 0 if m.group(2) else 1
            j += 1
        for kind, name in (('b', 'birth'), ('d', 'death')):
            if kind in ev:
                _set_event(c, name, *ev[kind])
        rows.append(c)
    return rows


# --- scoring ----------------------------------------------------------------------------------------

def parse_facts(s):
    f = {}
    for part in (s or '').split():
        if '=' in part:
            k, v = part.split('=', 1)
            f[k] = v.replace('_', ' ')
    return f


def _surname_at(s, tokens):
    """Index of the first token of a whole surname in tokens, or None: every significant token present,
    or an exact split (Le Chat = Lechat), never a substring."""
    sig = [w for w in s if w not in NAME_PARTICLES] or s
    if s and all(w in tokens for w in sig):
        return min(tokens.index(w) for w in sig)
    joined = ''.join(s)
    for i in range(len(tokens)):
        for j in range(i + 1, min(len(tokens), i + 3) + 1):
            if ''.join(tokens[i:j]) == joined:
                return i
    return None


def _surname_in(s, tokens):
    return _surname_at(s, tokens) is not None


def _place_match(want, have):
    """The most specific part of the tree's place (before the first comma) must be in the row's place.
    A row that only says 'Spain' never matches 'Murcia, Spain'; a shared 'San' or 'La' never counts."""
    first = norm(want.split(',')[0])
    sig = [w for w in first if w not in PLACE_GENERIC] or first
    tokens = norm(have)
    return bool(sig) and all(w in tokens for w in sig)


def _spouse_match(want, spouses):
    """Given name and a surname of the tree's spouse, both inside ONE spouse entry of the row."""
    w = norm(want)
    rest = [t for t in w[1:] if t not in NAME_PARTICLES]
    if not rest:
        return None
    for s in spouses:
        t = norm(s)
        if w[0] in t and any(r in t for r in rest):
            return s
    return False


def score(c, given, surnames, facts, spouse='', window=3):
    """(tier, points, reasons) for one row. Never drops a row: a rejection is a tier, not a cut."""
    rn = norm(c['name'])
    rs = norm(' '.join(c['surnames'])) if c.get('surnames') is not None else rn
    ev = c.get('events') or {}
    sur = [norm(s) for s in surnames if norm(s)]
    parts = c['name'].split(',')
    while len(parts) > 1 and all(w in NAME_SUFFIXES for w in norm(parts[-1])):   # 'Juan Garcia,' or ', Jr'
        parts.pop()
    head, comma, tail = ','.join(parts[:-1]), len(parts) > 1, parts[-1]
    if c.get('given') is not None:
        rg = norm(c['given'])
    elif comma:   # surname first, split at the last comma: 'Garcia Martin, Juan', 'Garcia, Martin, Juan'
        rg = norm(tail)
        if c.get('surnames') is None:
            rs = norm(head)
    else:   # only the full name: the given name is what comes before the first surname, at least one word
        at = [i for i in (_surname_at(x, rn) for x in sur) if i is not None]
        rg = rn[:max(1, min(at))] if at else rn
    g = [w for w in norm(given) if w not in NAME_PARTICLES]
    pts, why, agree, conflict = 0, [], 0, False

    s1 = bool(sur) and _surname_in(sur[0], rs)
    s2 = len(sur) > 1 and _surname_in(sur[1], rs)
    if s1:
        pts += 2 + (1 if s2 else 0); why.append('surname1' + ('+2' if s2 else ''))
    elif s2:
        pts += 1; why.append('surname2 only')
    elif sur:
        why.append('surname differs')
    else:
        why.append('no surname to check')
    gm = None
    if g and g[0] in rg:
        gm = 'given'
    elif g and any(w in rg for w in g[1:]):
        gm = 'given (other part)'
    elif g and any(len(w) >= 4 and difflib.SequenceMatcher(None, g[0], w).ratio() >= 0.8 for w in rg):
        gm = 'given variant'
    if gm:
        pts += 1 if gm == 'given variant' else 2; why.append(gm)
    else:
        why.append('given differs' if g else 'no given name to check')

    for key, kind in (('by', 'birth'), ('dy', 'death')):
        want, have = years(facts.get(key, '')), _year_range(ev.get(kind))
        if not want or not have:
            continue
        w = want[0]
        if have[0] - window <= w <= have[1] + window:
            agree += 1; exact = have[0] == have[1] == w
            pts += 3 if exact else 2; why.append('%s %s%s%s' % (key, have[0], '=' if exact else '~', w))
        else:
            conflict = True; why.append('%s %s contradicts %s' % (key, have[0], w))
    for key, kind in (('bp', 'birth'), ('dp', 'death')):   # each place against its own event, never across
        have = (ev.get(kind) or {}).get('place')
        if facts.get(key) and have and _place_match(facts[key], have):
            agree += 1; pts += 2; why.append('%s %s' % (key, facts[key]))
    if spouse:
        m = _spouse_match(spouse, [x['name'] for x in c.get('relatives') or [] if x.get('role') == 'spouse'])
        if m is None:
            why.append('spouse needs a given name and a surname')
        elif m:
            agree += 1; pts += 2; why.append('spouse ' + m)
    if not c['principal']:
        why.append('matched as %s, not the principal' % (c['role'] or 'a relative'))

    name_ok = s1 and gm in ('given', 'given (other part)')
    if conflict:
        tier = 'conflict'
    elif name_ok and c['principal'] and agree >= 2:
        tier = 'strong'
    elif (s1 or s2) and gm and agree >= 1:
        tier = 'possible'
    else:
        tier = 'weak'
    return tier, pts, why


def load(path):
    """Records from one file ('-' for stdin): search JSON or normalized records when it parses, else page text."""
    text = sys.stdin.read() if path == '-' else open(path, encoding='utf-8').read()
    try:
        doc = json.loads(text)
    except ValueError:
        return text_rows(text)
    if isinstance(doc, dict) and 'candidates' in doc:
        return normalized_rows(doc)
    if not isinstance(doc, dict) or 'entries' not in doc:
        raise ValueError('%s is JSON but neither a FamilySearch search result nor {"candidates": [...]}' % path)
    return gedcomx_rows(doc)


def rank(rows, given, surnames, facts, spouse=''):
    out = []
    for i, c in enumerate(rows):
        tier, pts, why = score(c, given, surnames, facts, spouse)
        b, d = c['events'].get('birth') or {}, c['events'].get('death') or {}
        out.append({'tier': tier, 'score': pts, 'source': c['source'], 'name': c['name'], 'role': c['role'], 'id': c['id'],
                    'by': b.get('year'), 'bp': b.get('place', ''), 'dy': d.get('year'), 'dp': d.get('place', ''),
                    'relatives': c['relatives'], 'why': why, 'n': i + 1})
    out.sort(key=lambda r: (TIERS.index(r['tier']), -r['score'], r['n']))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-rank', description=__doc__.split('\n')[0])
    p.add_argument('files', nargs='+', help="search JSON or page text; '-' reads stdin")
    p.add_argument('--given', default='', help='given name(s) as in the tree')
    p.add_argument('--surname', action='append', default=[], help='first surname, then again for the second')
    p.add_argument('--facts', default='', help="'by=1901 bp=Madrid dy=1970 dp=Murcia', spaces in places as _")
    p.add_argument('--spouse', default='', help='spouse given name and surname')
    p.add_argument('--format', choices=('tsv', 'json'), default='tsv')
    a = p.parse_args(argv)
    if not a.given and not a.surname:
        p.error('give --given, --surname or both')
    rows = []
    try:
        for f in a.files:
            rows += load(f)
        res = rank(rows, a.given, a.surname, parse_facts(a.facts), a.spouse)
    except (OSError, ValueError, UnicodeDecodeError, TypeError, AttributeError, KeyError) as exc:
        # a malformed file is "nothing read" (2), never "rows read, none strong" (1)
        print('gwt-rank: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    if not rows:
        print('gwt-rank: no rows to rank: 0 results, not a results page, or not loaded', file=sys.stderr)
        return 2
    for r in res:
        if a.format == 'json':
            print(json.dumps(r, ensure_ascii=False))
        else:
            print('\t'.join(re.sub(r'[\t\r\n]+', ' ', str(v)) for v in (r['tier'], r['score'], r['id'], r['name'], r['role'], r['by'] or '', r['bp'],
                                            r['dy'] or '', r['dp'], '; '.join(r['why']))))
    counts = {t: sum(r['tier'] == t for r in res) for t in TIERS}
    print('gwt-rank: %d rows, %s' % (len(res), ', '.join('%d %s' % (counts[t], t) for t in TIERS)), file=sys.stderr)
    return 0 if counts['strong'] else 1


if __name__ == '__main__':
    sys.exit(main())
