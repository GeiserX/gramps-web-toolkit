#!/usr/bin/env python3
"""gwt-rank: order the rows of a record search by how well they fit a person in the tree.

A search by name returns thousands of rows, and a lane that stops at "N results" has found nothing.
This reads the rows and puts the ones that agree with what the tree already knows first. It never
hides a row: every row is printed with a tier, so a page with no strong row is still a list of leads.

The input is the FamilySearch search JSON (GEDCOM X, the body `gwt-fscache` stores for a
`/service/search/hr/` URL). It carries no language, marks the principal of each record and names
each spouse and parent explicitly. A file that is not JSON is read as the visible text of a results
page (MyHeritage, other sites), with Spanish or English labels.

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


def candidate(name, role, principal, given=None, surname=None):
    return {'name': name, 'role': role, 'principal': principal, 'given': given, 'surname': surname,
            'by': None, 'bp': '', 'dy': None, 'dp': '', 'spouses': [], 'id': ''}


# --- GEDCOM X search JSON -------------------------------------------------------------------------

def _name(person):
    for n in person.get('names') or []:
        for form in n.get('nameForms') or []:
            given = [p.get('value', '') for p in form.get('parts') or [] if p.get('type') == GX + 'Given']
            sur = [p.get('value', '') for p in form.get('parts') or [] if p.get('type') == GX + 'Surname']
            full = form.get('fullText') or ' '.join(given + sur)
            if full:
                return full, (norm(' '.join(given)) if given or sur else None), (norm(' '.join(sur)) if given or sur else None)
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
    """One candidate per entry: the persona the search matched, with only its own facts."""
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
            c = candidate(full, role, bool(p.get('principal')), given, sur)
            facts = p.get('facts') or []
            c['by'], c['bp'] = _event(facts, BIRTH_TYPES)
            c['dy'], c['dp'] = _event(facts, DEATH_TYPES)
            ref = '#' + p.get('id', '')
            for r in g.get('relationships') or []:
                if r.get('type') != GX + 'Couple':
                    continue
                ends = [(r.get('person1') or {}).get('resource'), (r.get('person2') or {}).get('resource')]
                if ref in ends:
                    other = by_ref.get(ends[1] if ends[0] == ref else ends[0])
                    if other:
                        c['spouses'].append(_name(other)[0])
            c['id'] = eid
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
        c = candidate(lines[i][0], lines[i + 1][0], key[i + 1] in PRINCIPAL)
        j = 1   # body[0] is the collection title: 'Baptism Register 1850-1900' is not an event
        while j < len(body):
            line = body[j]
            kind = 'b' if BIRTH_TEXT.match(line) else 'd' if DEATH_TEXT.match(line) else None
            if kind:
                # the year sits on the event line, or on the next line when the date is on its own line
                k = j if years(line) else (j + 1 if j + 1 < len(body) and years(body[j + 1]) else None)
                if k is not None and c[kind + 'y'] is None:
                    c[kind + 'y'] = years(body[k])
                    if k + 1 < len(body) and not NOT_A_PLACE.match(body[k + 1]):
                        c[kind + 'p'] = body[k + 1]
                    j = k
            m = re.match(SPOUSE_LABEL + r'\s*:?\s*(.*)$', line, re.I)
            if m:
                value = m.group(2) or (body[j + 1] if j + 1 < len(body) else '')
                c['spouses'] += [s for s in re.split(r'\s*[,;]\s*|\s+(?:and|y)\s+', value) if s.strip()]
                j += 0 if m.group(2) else 1
            j += 1
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


def _surname_in(s, tokens):
    """A whole surname: every significant token present, or an exact split (Le Chat = Lechat)."""
    sig = [w for w in s if w not in NAME_PARTICLES] or s
    if s and all(w in tokens for w in sig):
        return True
    joined = ''.join(s)
    return any(''.join(tokens[i:j]) == joined for i in range(len(tokens)) for j in range(i + 1, min(len(tokens), i + 3) + 1))


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
    rg = c['given'] if c['given'] is not None else rn
    rs = c['surname'] if c['surname'] is not None else rn
    sur = [norm(s) for s in surnames if norm(s)]
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

    for key in ('by', 'dy'):
        want, have = years(facts.get(key, '')), c[key]
        if not want or not have:
            continue
        w = want[0]
        if have[0] - window <= w <= have[1] + window:
            agree += 1; exact = have[0] == have[1] == w
            pts += 3 if exact else 2; why.append('%s %s%s%s' % (key, have[0], '=' if exact else '~', w))
        else:
            conflict = True; why.append('%s %s contradicts %s' % (key, have[0], w))
    for key in ('bp', 'dp'):   # each place against its own event: birth place to birth, death place to death
        if facts.get(key) and c[key] and _place_match(facts[key], c[key]):
            agree += 1; pts += 2; why.append('%s %s' % (key, facts[key]))
    if spouse:
        m = _spouse_match(spouse, c['spouses'])
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
    """Rows from one file ('-' for stdin): GEDCOM X search JSON when it parses, else page text."""
    text = sys.stdin.read() if path == '-' else open(path, encoding='utf-8').read()
    try:
        doc = json.loads(text)
    except ValueError:
        return text_rows(text)
    if not isinstance(doc, dict) or 'entries' not in doc:
        raise ValueError('%s is JSON but not a FamilySearch search result' % path)
    return gedcomx_rows(doc)


def rank(rows, given, surnames, facts, spouse=''):
    out = []
    for i, c in enumerate(rows):
        tier, pts, why = score(c, given, surnames, facts, spouse)
        out.append({'tier': tier, 'score': pts, 'name': c['name'], 'role': c['role'], 'id': c['id'],
                    'by': c['by'] and c['by'][0], 'bp': c['bp'], 'dy': c['dy'] and c['dy'][0], 'dp': c['dp'],
                    'spouses': c['spouses'], 'why': why, 'n': i + 1})
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
    for f in a.files:
        try:
            rows += load(f)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            print('gwt-rank: %s' % exc, file=sys.stderr)
            return 2
    if not rows:
        print('gwt-rank: no rows to rank: 0 results, not a results page, or not loaded', file=sys.stderr)
        return 2
    res = rank(rows, a.given, a.surname, parse_facts(a.facts), a.spouse)
    for r in res:
        if a.format == 'json':
            print(json.dumps(r, ensure_ascii=False))
        else:
            print('\t'.join(str(v) for v in (r['tier'], r['score'], r['id'], r['name'], r['role'], r['by'] or '', r['bp'],
                                            r['dy'] or '', r['dp'], '; '.join(r['why']))))
    counts = {t: sum(r['tier'] == t for r in res) for t in TIERS}
    print('gwt-rank: %d rows, %s' % (len(res), ', '.join('%d %s' % (counts[t], t) for t in TIERS)), file=sys.stderr)
    return 0 if counts['strong'] else 1


if __name__ == '__main__':
    sys.exit(main())
