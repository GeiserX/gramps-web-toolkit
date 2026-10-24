#!/usr/bin/env python3
"""gwt_mh: the pure logic of gwt-mh, the MyHeritage tool. Standard library only, no network, no browser.

What lives here: search and record URL builders, the URL normalizer used as the cache key, the
normalizer from MyHeritage's own search JSON to the candidate records gwt-rank reads, the record page
parser, wall and paywall detection, plan inference from what pages showed, image-path and image-header
decoding, the fetch manifest, ledger source slugs and the FamilySearch-mirror verdict.

The browser side (gwt_mh_browser.py) captures pages and hands them here as an envelope:
    {"url", "final_url", "text", "html", "graphql": [{"url", "body"}], "captured_at", "plan"}
"""
import datetime, hashlib, html, json, math, os, re, struct, unicodedata, urllib.parse
from html.parser import HTMLParser

HOST = 'https://www.myheritage.es'
EXIT_OK, EXIT_EMPTY, EXIT_WALL, EXIT_DEAD, EXIT_PAYWALL = 0, 2, 3, 4, 5
DAY = 86400

# Cache rules for gwt-fscache, first match wins, None = never expires.
MYHERITAGE_RULES = (
    (r'action=showRecord', None),                    # a published record does not change
    (r'original_records/', None),
    (r'\.(jpe?g|png|tiff?|gif|pdf)(\?|$)', None),
    (r'/discovery-hub', 1 * DAY),                    # matches move as trees change
    (r'action=query', 7 * DAY),                      # searches, also inside one collection
    (r'records-catalog|/research/collection-\d+', 30 * DAY),
)
# Query parameters that change per session or per click and never change the answer.
DROP_PARAMS = {'perm_id', 'recordtitle', 'csrf_token', 'fbclid', 'gclid', '_ga'}
PLAN_HEADER = 'x-gwt-mh-plan'

# ---------------------------------------------------------------- small helpers


def norm_words(text):
    t = unicodedata.normalize('NFKD', text or '')
    t = ''.join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9 ]+', ' ', t).split()


def slug(text, limit=60):
    return '-'.join(norm_words(text))[:limit].strip('-') or 'x'


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def write_atomic(path, data):
    """Write to path.part, then rename: a reader never sees half a file."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data if isinstance(data, bytes) else data.encode('utf-8'))
    os.replace(tmp, path)


def write_json(path, obj):
    write_atomic(path, json.dumps(obj, ensure_ascii=False, indent=1) + '\n')


# ---------------------------------------------------------------- URLs


def normalize_url(url):
    """The cache key form of a URL: lower-case host, query sorted, session and tracking params dropped."""
    p = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if k.lower() not in DROP_PARAMS and not k.lower().startswith(('utm_', 'tr_'))]
    query = urllib.parse.urlencode(sorted(q), quote_via=urllib.parse.quote)
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, query, ''))


def _q(value):
    return urllib.parse.quote_plus(value, safe='/*.')


def _name_term(given='', surnames=()):
    parts = ['Name']
    if given:
        parts.append('fn.' + given)
    sur = [s for s in surnames if s]
    if sur:
        parts.append('ln.' + '/3'.join(sur))     # the site joins a first and second surname with /3
    return ' '.join(parts)


def _split_name(full):
    """'Juan Ruiz Perez' -> ('Juan', ['Ruiz', 'Perez']): first word given, the rest surnames."""
    w = (full or '').split()
    return (w[0], w[1:]) if w else ('', [])


def search_url(given='', surnames=(), by=None, dy=None, spouse='', father='', mother='', collection=None,
               exact=False, host=HOST):
    """The URL the site's own search form opens, in the shape observed on the site in 2026."""
    path = '/research' + ('/collection-%s/x' % collection if collection else '')
    params = [('formId', 'master'), ('formMode', '1'), ('action', 'query'), ('exactSearch', '1' if exact else ''),
              ('useTranslation', '1'), ('qname', _name_term(given, surnames))]
    events = []
    if by:
        events.append(('qevents-event1', 'Event et.birth ey.%d' % int(by)))
    if dy:
        events.append(('qevents-any/1event_2', 'Event et.death ey.%d' % int(dy)))
    if events:
        params += events + [('qevents', 'List')]
    if spouse:
        params.append(('qspouse', _name_term(*_split_name(spouse))))
    rels = [(r, n) for r, n in (('father', father), ('mother', mother)) if n]
    for i, (role, name) in enumerate(rels):
        if i == 0:
            params += [('qrelatives-relative', 'Relative rt.%s rn.*qrelative_relativeName' % role),
                       ('qrelative_relativeName', _name_term(*_split_name(name)) + ' lnmsrs.false')]
        else:
            params += [('qrelatives-addRelative_%d' % i, 'Relative rt.%s rn.*qaddRelative_%d_addRelativeName' % (role, i)),
                       ('qaddRelative_%d_addRelativeName' % i, _name_term(*_split_name(name)) + ' lnmsrs.false')]
    if rels:
        params.append(('qrelatives', 'List'))
    return host + path + '?' + '&'.join('%s=%s' % (k, _q(v)) for k, v in params)


def record_url(collection, item, host=HOST):
    return '%s/research/collection-%s/x?itemId=%s&action=showRecord' % (host, collection, urllib.parse.quote(item, safe='-_'))


def matches_url(site, person, kind='record', status='pending', tree='1', host=HOST):
    """A person's Record Matches (matchType 4) or Smart Matches (2), pending (32) or confirmed (8)."""
    return '%s/discovery-hub/%s/matches-for-person/%s?matchType=%d&matchStatus=%d&confirmedByOthers=0&treeId=%s' % (
        host, site, person, 4 if kind == 'record' else 2, 32 if status == 'pending' else 8, tree)


def catalog_url(page=1, host=HOST):
    return host + '/research/records-catalog' + ('?p=%d' % page if page > 1 else '')


def ids_from_link(link):
    """(collection id, item id) from a showRecord or paywall link, else (None, None)."""
    p = urllib.parse.urlsplit(link or '')
    q = dict(urllib.parse.parse_qsl(p.query))
    m = re.search(r'/collection-(\d+)', p.path)
    cid = m.group(1) if m else q.get('colId')
    return cid, q.get('itemId')


# ---------------------------------------------------------------- page classification


# Bot walls and account lockouts. Matched case-insensitively, and before the /login test: the 24 h lockout
# is served on /login, and reading it as "logged out" invites the new login that keeps the lock going.
WALL_MARKERS = ('_Incapsula_Resource', 'Incapsula incident ID', 'Security check', 'Control de Seguridad',
                'hCaptcha', 'actividad inusual', 'unusual activity', 'actividad irregular', 'irregular activity',
                'desactivado temporalmente', 'temporarily disabled')
LOGIN_MARKERS = ('Verifique su correo', 'Verify your email', 'Ingrese el código', 'Enter the code')
SIGNED_IN_MARKERS = ('Opciones de cuenta', 'Account options')


def classify(final_url, text='', html_text=''):
    """'wall' (bot check: stop, exit 3), 'dead' (logged out: exit 4), 'paywall' (exit 5) or 'ok'."""
    blob = (text or '') + '\n' + (html_text or '')
    low = blob.lower()
    if any(m.lower() in low for m in WALL_MARKERS):
        return 'wall'
    path = urllib.parse.urlsplit(final_url or '').path.lower()
    if '/login' in path or any(m in (text or '') for m in LOGIN_MARKERS):
        return 'dead'
    if path.startswith('/paywall'):
        return 'paywall'
    if text and re.search(r'\b(Iniciar sesión|Log in)\b', text) and not any(m in text for m in SIGNED_IN_MARKERS):
        return 'dead'
    return 'ok'


EXIT_FOR = {'ok': EXIT_OK, 'wall': EXIT_WALL, 'dead': EXIT_DEAD, 'paywall': EXIT_PAYWALL}

# ---------------------------------------------------------------- secrets never reach disk

# Session secrets a page or a search carries: the search bearer token, the FamilyGraph tokens embedded in
# the page script (fgToken, webClientFgToken, ...), the XSRF and CSRF tokens, and Authorization headers.
_SECRET_NAME = r'\w*(?:fgtoken|xsrftoken|csrftoken|csrf_token|bearer_token|bearertoken|access_token|accesstoken)'
_SECRET_KEY = re.compile(r'^%s$|^authorization$' % _SECRET_NAME, re.I)
_SECRET_RES = (
    re.compile(r'(%s(?:\\*["\'])?\s*[:=]\s*(?:\\*["\'])?)([^"\'&\s,;}\\<]+)' % _SECRET_NAME, re.I),
    re.compile(r'(name="bearer_token"\r?\n\r?\n)([^\r\n]+)'),
    re.compile(r'(\b[Bb]earer\s+)([A-Za-z0-9._~+/=-]{16,})'),
    re.compile(r'((?:[Aa]uthorization)["\']?\s*[:=]\s*["\']?)([^"\'\r\n,}]+)'),
)


def scrub(obj):
    """A copy with every session token (bearer, FamilyGraph, XSRF, CSRF) and Authorization value replaced by REDACTED."""
    if isinstance(obj, dict):
        return {k: ('REDACTED' if _SECRET_KEY.match(k) else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        for rx in _SECRET_RES:
            obj = rx.sub(lambda m: m.group(1) + 'REDACTED', obj)
        return obj
    return obj


# ---------------------------------------------------------------- search JSON -> candidates

_MASK = re.compile(r'<a\b[^>]*class="nonFreeComponentLink"[^>]*>(.*?)</a>', re.S | re.I)
_TAG = re.compile(r'<[^>]+>')
YEAR = re.compile(r'(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)')
EVENT_FIELDS = {'birth': 'birth', 'baptism': 'birth', 'christening': 'birth', 'death': 'death', 'burial': 'death'}
REL_FIELDS = {'parents': 'parent', 'parent': 'parent', 'father': 'father', 'mother': 'mother',
              'spouse': 'spouse', 'spouses': 'spouse', 'wife': 'spouse', 'husband': 'spouse',
              'children': 'child', 'child': 'child', 'son': 'child', 'daughter': 'child',
              'siblings': 'other', 'sibling': 'other'}
MASK_WORDS = {'dia': 'day', 'day': 'day', 'mes': 'month', 'month': 'month', 'lugar': 'place', 'place': 'place',
              'ano': 'year', 'year': 'year'}


def _plain(value):
    v = re.sub(r'<br\s*/?>', '\n', value or '', flags=re.I)
    v = html.unescape(_TAG.sub('', v)).replace('\xa0', ' ')
    return '\n'.join(' '.join(line.split()) for line in v.split('\n')).strip()


def is_masked_value(value):
    """A value the server replaced with a paywall link for a records plan."""
    return any('subscription_type=data' in m.group(0) for m in _MASK.finditer(value or '')) or \
        ('nonFreeComponentLink' in (value or '') and 'paywall' in (value or ''))


def split_masked(value):
    """(the value with paywall links removed, [the words the links showed])."""
    words = [_plain(m.group(1)) for m in _MASK.finditer(value or '')]
    rest = _MASK.sub('\x00', value or '')
    rest = re.sub(r'\s+(?:y|and|e)\s*\x00', '\x00', rest)   # "Ana Ruiz y <paywall: 3 more>" keeps Ana Ruiz
    return _plain(rest.replace('\x00', ' ')), words


def parse_date_place(text):
    """'4 de junio de 1892 - Córdoba, Spain' -> ({'year', 'year_to'} or None, place, date text)."""
    date, _, place = (text or '').partition(' - ')
    if not place and '\n' in date:
        date, _, place = date.partition('\n')
    ys = [int(y) for y in YEAR.findall(date)]
    if not ys and not place and not YEAR.search(text or ''):
        return None, (text or '').strip(), ''
    yr = {'year': min(ys), 'year_to': max(ys)} if ys else None
    return yr, ' '.join(place.split()), date.strip()


def _split_people(value):
    return [n for n in (x.strip(' ,;') for x in re.split(r'\n|,\s+|;\s*', value or '')) if n]


def _image_path_hint(url):
    """Newspaper title, date, issue and page from an OldNews thumbnail or original image path."""
    m = re.search(r'/([^/]+)/(\d{4})/(\d{2})/(\d{2})/([^/]+)/(\d+)(?:_\d+x_[0-9a-f]+)?\.(?:jpe?g|png)(?:$|\?)',
                  urllib.parse.urlsplit(url or '').path, re.I)
    if not m:
        return None
    title, y, mo, d, issue, page = m.groups()
    return {'title': title, 'date': '%s-%s-%s' % (y, mo, d), 'issue': re.sub(r'_jpg$', '', issue), 'page': int(page)}


image_hint = _image_path_hint


def source_slug(collection_id, collection_name=''):
    if str(collection_id) == '12040' or 'oldnews' in (collection_name or '').lower():
        return 'myheritage-oldnews'
    return 'myheritage'


def mirror_slug(collection_id):
    """The ledger source of a search in a MyHeritage collection that mirrors a FamilySearch one.
    The ledger compares sources as exact slugs, so this never counts as the FamilySearch search."""
    return 'myheritage-espejo-fs-%s' % re.sub(r'[^0-9a-z]+', '-', str(collection_id).lower()).strip('-')


def graphql_records(envelope_or_bodies):
    """Every search record in an envelope's captured GraphQL answers, deduplicated by record id, plus
    the total count the site reported (None when absent)."""
    bodies = envelope_or_bodies.get('graphql', []) if isinstance(envelope_or_bodies, dict) else envelope_or_bodies
    seen, out, total = set(), [], None
    for g in bodies:
        body = g.get('body') if isinstance(g, dict) and 'body' in g else g
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                continue
        resp = (((body or {}).get('data') or {}).get('search_query_upload') or {}).get('response') or {}
        res = resp.get('results') or {}
        if res.get('count') is not None:
            total = res['count']
        for x in res.get('data') or []:
            rec = x.get('record') or {}
            rid = rec.get('id')
            if rid and rid not in seen:
                seen.add(rid)
                out.append(x)
    return out, total


def candidate(item):
    """One search result (the `data[]` entry of the site's search JSON) as a gwt-rank candidate record.
    A value the plan hides becomes null or "" and its field name goes to mh.masked."""
    rec = item.get('record') or item
    col = rec.get('collection') or {}
    cid = (col.get('id') or '').replace('collection-', '') or ids_from_link(rec.get('link'))[0] or ''
    _, item_id = ids_from_link(rec.get('link'))
    item_id = item_id or rec.get('id', '')
    name = _plain(rec.get('name') or '')
    c = {'source': source_slug(cid, col.get('name')), 'id': '%s:%s' % (cid, item_id), 'name': name,
         'role': 'principal', 'principal': True, 'given': None, 'surnames': None, 'events': {}, 'relatives': []}
    thumb = ((rec.get('thumbnail') or {}).get('url')) or ''
    mh = {'collection_id': cid, 'collection': col.get('name', ''), 'is_free': bool(rec.get('is_free')),
          'collection_is_free': bool(col.get('is_free')), 'has_images': bool(col.get('has_images')),
          'record_type': item.get('record_type', ''), 'masked': [],
          'link': normalize_url(rec['link']) if rec.get('link') else '', 'thumbnail': normalize_url(thumb) if thumb else ''}
    hint = _image_path_hint(thumb)
    if hint:
        mh['image_hint'] = hint
    for f in rec.get('display_fields') or []:
        fname, raw = (f.get('name') or '').lower(), f.get('value') or ''
        value, masked_words = split_masked(raw)
        if fname in EVENT_FIELDS:
            kind = EVENT_FIELDS[fname]
            yr, place, _ = parse_date_place(value)
            for w in masked_words:
                part = MASK_WORDS.get(' '.join(norm_words(w)))
                if part:
                    mh['masked'].append('%s.%s' % (kind, part))
            if kind not in c['events'] and (yr or place):
                c['events'][kind] = {'year': yr and yr['year'], 'year_to': yr and yr['year_to'], 'place': place}
        elif fname in REL_FIELDS:
            if masked_words:
                mh['masked'].append(fname)
            c['relatives'] += [{'role': REL_FIELDS[fname], 'name': n} for n in _split_people(value)]
        elif fname == 'text':
            mh['ocr_snippet'] = value
        elif fname == 'article-summary':
            mh['summary'] = value
        elif fname == 'publication-title':
            mh['paper'] = value
        elif fname == 'publication':
            _, place, date = parse_date_place(value)
            mh['pub_date'], mh['pub_place'] = date, place
        else:
            if masked_words:
                mh['masked'].append(fname)
            mh.setdefault('fields', {})[fname] = value
    mh['masked'] = sorted(set(mh['masked']))
    c['mh'] = mh
    return c


def candidates_from_envelope(envelope):
    recs, total = graphql_records(envelope)
    return [candidate(x) for x in recs], total


# ---------------------------------------------------------------- record page (showRecord)


class _TextOf(HTMLParser):
    """innerText-like text of saved HTML: cells joined by tabs, rows and blocks by new lines."""
    BLOCK = {'p', 'div', 'tr', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'section', 'header', 'footer', 'ul'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        elif tag in ('td', 'th'):
            self.out.append('\t')
        elif tag in self.BLOCK:
            self.out.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(' '.join(data.split()) if data.strip() else '')


def html_to_text(html_text):
    p = _TextOf()
    p.feed(html_text or '')
    lines = []
    for line in ''.join(p.out).split('\n'):
        cells = [c.strip() for c in line.split('\t')]
        while cells and not cells[0]:
            cells.pop(0)
        if cells:
            lines.append('\t'.join(cells))
    return '\n'.join(lines)


BACK = ('Volver a los resultados de la búsqueda', 'Back to search results')
FIELDS_END = ('¿Descubrió un error?', 'Found a mistake?', 'Cite este registro', 'Cite this record')
CITE = ('Cite este registro', 'Cite this record')
MENTIONED = ('Personas mencionadas en este artículo', 'People mentioned in this article')
RELATIVES_HEAD = ('Familiares', 'Relatives', 'Household members', 'Miembros del hogar')
FULL_PAGE = ('Vea la página completa', 'See the full page', 'View the full page')
IN_PLAN = ('incluido en su plan', 'included in your plan')
PROVIDER = re.compile(r'^(Este registro es proporcionado por|This record is provided by)\s+(.*)$')
COUNT = re.compile(r'^([\d.,]+)\s+(REGISTROS|RECORDS)$', re.I)
LABELS = {  # page label -> field; Spanish and English
    'nombre': 'name', 'name': 'name', 'genero': 'gender', 'gender': 'gender', 'sexo': 'gender',
    'nacimiento': 'birth', 'birth': 'birth', 'bautismo': 'baptism', 'bautizo': 'baptism', 'baptism': 'baptism',
    'christening': 'baptism', 'fallecimiento': 'death', 'death': 'death', 'defuncion': 'death',
    'entierro': 'burial', 'burial': 'burial', 'matrimonio': 'marriage', 'marriage': 'marriage',
    'residencia': 'residence', 'residence': 'residence', 'edad': 'age', 'age': 'age',
    'ocupacion': 'occupation', 'occupation': 'occupation', 'occupations': 'occupation',
    'padre': 'father', 'father': 'father', 'madre': 'mother', 'mother': 'mother', 'padres': 'parents', 'parents': 'parents',
    'esposo': 'spouse', 'esposa': 'spouse', 'conyuge': 'spouse', 'spouse': 'spouse', 'husband': 'spouse', 'wife': 'spouse',
    'hijo': 'children', 'hija': 'children', 'hijos': 'children', 'children': 'children', 'child': 'children',
    'hermanos': 'siblings', 'siblings': 'siblings',
    'resumen del articulo': 'summary', 'article summary': 'summary', 'periodico': 'paper', 'newspaper': 'paper',
    'publicacion': 'publication', 'publication': 'publication', 'texto': 'text', 'text': 'text',
    'temas del articulo': 'topics', 'article topics': 'topics',
}
REL_ROLE = {'father': 'father', 'mother': 'mother', 'parents': 'parent', 'spouse': 'spouse', 'children': 'child',
            'siblings': 'other'}
ROW_ROLE = {'padre': 'father', 'father': 'father', 'madre': 'mother', 'mother': 'mother', 'esposo': 'spouse',
            'esposa': 'spouse', 'conyuge': 'spouse', 'spouse': 'spouse', 'wife': 'spouse', 'husband': 'spouse',
            'hijo': 'child', 'hija': 'child', 'son': 'child', 'daughter': 'child', 'child': 'child'}


def parse_record_text(text):
    """The fields of a showRecord page from its visible text ('Label<TAB>value' lines; a line with no
    tab right under a field continues it, which is how the page shows the place under a date). Returns a dict with title, collection, fields, relatives, mentioned,
    provider, record_count, oldnews_link and oldnews_included."""
    lines = (text or '').split('\n')
    out = {'title': '', 'collection': '', 'fields': {}, 'relatives': [], 'mentioned': [], 'provider': '',
           'record_count': None, 'collection_blurb': '', 'oldnews_link': False, 'oldnews_included': False}
    i = next((n for n, l in enumerate(lines) if l.strip().startswith(BACK)), -1)
    if i >= 0 and i + 2 < len(lines):
        out['title'] = lines[i + 1].strip()
        col = lines[i + 2].strip()
        out['collection'] = re.sub(r'^(En|In)\s+', '', col)
    state, last = 'fields', None
    for n in range(max(i, 0), len(lines)):
        raw = lines[n]
        line = raw.strip()
        if any(p in line for p in FULL_PAGE):
            out['oldnews_link'] = True
            out['oldnews_included'] = out['oldnews_included'] or any(p in line for p in IN_PLAN)
            continue
        if line.startswith(FIELDS_END):
            state = 'tail'
            if line.startswith(CITE):
                state = 'cite'
            continue
        if line.startswith(MENTIONED):
            state = 'mentioned'
            continue
        if line in RELATIVES_HEAD:
            state = 'relatives'
            continue
        if state == 'fields':
            if '\t' in raw:
                label, _, value = raw.partition('\t')
                key = LABELS.get(' '.join(norm_words(label)))
                last = key
                if key:
                    out['fields'][key] = value.strip()
            elif last and line:
                # the line right under a field continues it: the place under a date (" \xa0Villazul")
                out['fields'][last] += '\n' + line
            else:
                last = None
        elif state == 'relatives':
            cells = [c.strip() for c in raw.split('\t')]
            if len(cells) >= 2 and cells[0] and cells[1]:
                role = ROW_ROLE.get(' '.join(norm_words(cells[0])))
                if role or ' '.join(norm_words(cells[0])) not in ('relacion', 'relationship'):
                    out['relatives'].append({'role': role or 'other', 'name': cells[1],
                                             'year': (YEAR.findall(cells[2]) or [None])[0] if len(cells) > 2 else None})
        elif state == 'mentioned':
            if line and line.upper() not in ('NOMBRE', 'NAME'):
                out['mentioned'].append(line)
        elif state == 'cite':
            m = PROVIDER.match(line)
            if m:
                out['provider'] = m.group(2)
                continue
            m = COUNT.match(line)
            if m:
                out['record_count'] = int(re.sub(r'[.,]', '', m.group(1)))
                continue
            if line.startswith(('BUSCAR EN ESTA COLECCIÓN', 'SEARCH THIS COLLECTION', 'Blog')):
                state = 'tail'
                continue
            if line and not out['collection_blurb'] and out['record_count'] is not None:
                out['collection_blurb'] = line
            elif line and out['record_count'] is None and not out['provider'] and line != out['collection']:
                out['collection'] = out['collection'] or line
    return out


def record_candidate(parsed, collection_id, item_id):
    """A parsed showRecord page as a gwt-rank candidate record (the principal, with its relatives)."""
    f = parsed['fields']
    c = {'source': source_slug(collection_id, parsed.get('collection')), 'id': '%s:%s' % (collection_id, item_id),
         'name': f.get('name') or parsed.get('title', ''), 'role': 'principal', 'principal': True,
         'given': None, 'surnames': None, 'events': {}, 'relatives': []}
    for kind, keys in (('birth', ('birth', 'baptism')), ('death', ('death', 'burial'))):
        for k in keys:
            if f.get(k):
                yr, place, _ = parse_date_place(f[k])
                if yr or place:
                    c['events'][kind] = {'year': yr and yr['year'], 'year_to': yr and yr['year_to'], 'place': place}
                    break
    for k, role in REL_ROLE.items():
        c['relatives'] += [{'role': role, 'name': n} for n in _split_people(f.get(k, ''))]
    c['relatives'] += [{'role': r['role'], 'name': r['name']} for r in parsed.get('relatives', [])]
    principal = ' '.join(norm_words(c['name']))
    c['relatives'] += [{'role': 'other', 'name': n} for n in parsed.get('mentioned', [])
                       if ' '.join(norm_words(n)) != principal]
    mh = {'collection_id': str(collection_id), 'collection': parsed.get('collection', ''), 'masked': [],
          'provider': parsed.get('provider', ''), 'record_count': parsed.get('record_count')}
    for src, dst in (('summary', 'summary'), ('paper', 'paper'), ('text', 'ocr_snippet'), ('occupation', 'occupation'),
                     ('residence', 'residence'), ('age', 'age'), ('gender', 'gender'), ('topics', 'topics')):
        if f.get(src):
            mh[dst] = f[src]
    if f.get('publication'):
        _, place, date = parse_date_place(f['publication'])
        mh['pub_date'], mh['pub_place'] = date, place
    c['mh'] = mh
    return c


def citation(collection, name, link, item_id, accessed=None, provider=''):
    """A source citation built from what the page shows, in the usual order: collection, site, record, URL, date."""
    accessed = accessed or datetime.date.today().isoformat()
    s = '"%s", database, MyHeritage (%s : accessed %s), record %s, %s' % (
        collection or 'MyHeritage collection', normalize_url(link) if link else HOST, accessed, item_id, name)
    if provider:
        s += '; provided by %s' % provider
    return s + '.\n'


# ---------------------------------------------------------------- match lists (discovery hub)

MATCH_HEAD = re.compile(r'^(Record Match|Smart Match)(?:\s+(?:de|from)\s+(.*))?$')
MATCH_END = ('Revisar coincidencia', 'Review match', 'Ver en el árbol', 'View in tree')


def parse_matches(text):
    """The match cards of a discovery-hub page: kind, source, name and its label/value lines. Read only."""
    out, cur = [], None
    for raw in (text or '').split('\n'):
        line = raw.strip()
        m = MATCH_HEAD.match(line)
        if m:
            cur = {'kind': m.group(1), 'source': m.group(2) or '', 'name': '', 'fields': {}}
            out.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith(MATCH_END):
            cur = None
            continue
        if not cur['name'] and line:
            cur['name'] = re.sub(r'(NUEVO|NEW)$', '', line).strip()
        elif '\t' in raw:
            label, _, value = raw.partition('\t')
            cur['fields'][label.strip()] = value.strip()
    return out


# ---------------------------------------------------------------- collection catalogue

_CARD = re.compile(r'data-automations="collection_card_collection-(\d+)"')


def _cls(html_text, cls):
    m = re.search(r'class="[^"]*\b%s\b[^"]*"[^>]*>(.*?)</(?:a|div|span)>' % re.escape(cls), html_text, re.S)
    return _plain(m.group(1)) if m else ''


def parse_catalog(html_text):
    """Catalogue rows (id, title, category, count, updated, has_images) from one records-catalog page:
    one card per collection, in the markup the site used in 2026. A card shows no region or years."""
    rows, seen = [], set()
    starts = [(m.start(), m.group(1)) for m in _CARD.finditer(html_text or '')]
    for n, (pos, cid) in enumerate(starts):
        if cid in seen:
            continue
        seen.add(cid)
        card = html_text[pos:starts[n + 1][0] if n + 1 < len(starts) else pos + 8000]
        count = re.sub(r'\D', '', _cls(card, 'record_count_label'))
        updated = re.sub(r'^[^:]*:\s*', '', _cls(card, 'last_updated_label'))
        rows.append({'id': cid, 'title': _cls(card, 'collection_card_title'),
                     'category': _cls(card, 'collection_card_category_name'), 'count': int(count) if count else '',
                     'updated': updated, 'has_images': 'yes' if re.search(r'class="[^"]*\bhasImage\b', card) else ''})
    return rows


def catalog_pages(html_text):
    """The highest page number the catalogue links to (?p=N), 1 when it has no pager."""
    return max([int(x) for x in re.findall(r'[?&]p=(\d+)', html_text or '')] + [1])


CATALOG_COLUMNS = ('id', 'title', 'category', 'count', 'updated', 'has_images')


def catalog_tsv(rows):
    out = ['\t'.join(CATALOG_COLUMNS)]
    out += ['\t'.join(re.sub(r'[\t\r\n]+', ' ', str(r.get(k, ''))) for k in CATALOG_COLUMNS) for r in rows]
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------- plan inference

CAPABILITIES = ('records', 'images', 'oldnews_pages', 'tree_review')


def search_observations(items, url=''):
    """Plan evidence from search results: a masked value proves no records plan; a non-free record outside
    OldNews whose event shows a day or a place (more than the year a free account sees) proves one."""
    obs = []
    for x in items:
        rec = x.get('record') or x
        col = rec.get('collection') or {}
        # OldNews results show their place even on a free account (seen 2026-10-24), so they prove nothing
        free = bool(rec.get('is_free')) or bool(col.get('is_free')) or \
            source_slug((col.get('id') or '').replace('collection-', ''), col.get('name')) == 'myheritage-oldnews'
        for f in rec.get('display_fields') or []:
            v = f.get('value') or ''
            if is_masked_value(v):
                obs.append(('records', False))
                break
            if not free and (f.get('name') or '').lower() in EVENT_FIELDS:
                plain = _plain(v)
                yr, place, date = parse_date_place(plain)
                if place or (yr and len(norm_words(date)) > 1):
                    obs.append(('records', True))
                    break
    if not obs:
        return []
    # one search page is one observation: any mask wins, the account cannot see paid values
    value = all(v for _, v in obs)
    return [{'signal': 'records', 'value': value, 'url': url}]


def record_observations(final_url, collection_free=None, parsed=None, has_images=None, viewer=None, url='',
                        collection=None):
    """Plan evidence from opening a showRecord page. An image observation belongs to one collection."""
    obs = []
    state = classify(final_url, '')
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(final_url or '').query)
    if state == 'paywall':
        if 'reason' in q and q['reason'][0] == '178':
            obs.append({'signal': 'tree_review', 'value': False, 'url': url})
        else:
            obs.append({'signal': 'records', 'value': False, 'url': url})
        return obs
    if collection_free is False:
        obs.append({'signal': 'records', 'value': True, 'url': url})
    if has_images and viewer is not None:
        obs.append({'signal': 'images', 'value': bool(viewer), 'url': url, 'collection': str(collection or '')})
    if parsed and parsed.get('oldnews_link') and parsed.get('oldnews_included'):
        obs.append({'signal': 'oldnews_pages', 'value': True, 'url': url})
    return obs


def image_collections(observations):
    """{collection id: whether its record page gave an original image}, newest observation per collection."""
    seen = {}
    for _, o in sorted(enumerate(observations), key=lambda x: (x[1].get('at', ''), x[0])):
        if o.get('signal') == 'images':
            seen[str(o.get('collection') or '')] = bool(o['value'])
    return seen


def capabilities(observations):
    """The newest observation of each signal wins (a plan can lapse or be renewed). `images` is a summary
    only: true when any collection gave an original, false when every one tried gave none. It never gates
    a fetch, because whether an image exists is a fact of each collection, not of the plan."""
    caps = {k: None for k in CAPABILITIES}
    for o in sorted(enumerate(observations), key=lambda x: (x[1].get('at', ''), x[0])):
        o = o[1]
        if o.get('signal') in caps and o['signal'] != 'images':
            caps[o['signal']] = bool(o['value'])
    per = image_collections(observations)
    if per:
        caps['images'] = any(per.values())
    return caps


def plan_label(caps):
    if caps.get('records') is False:
        return 'basic'
    if caps.get('records') and caps.get('oldnews_pages'):
        return 'omni'
    if caps.get('records'):
        return 'data+'
    return 'unknown'


class PlanState:
    """plan.json in the cache dir: every observation, appended; the capabilities are derived from them."""

    def __init__(self, cache_dir, clock=now_iso):
        self.path = os.path.join(os.path.expanduser(cache_dir), 'plan.json')
        self.clock = clock

    def observations(self):
        try:
            with open(self.path) as f:
                return json.load(f).get('observations', [])
        except (OSError, ValueError):
            return []

    def add(self, obs):
        if not obs:
            return
        all_obs = self.observations()
        for o in obs:
            all_obs.append(dict(o, at=o.get('at') or self.clock()))
        caps = capabilities(all_obs)
        write_json(self.path, {'observations': all_obs, 'capabilities': caps, 'plan': plan_label(caps)})

    def caps(self):
        return capabilities(self.observations())

    def label(self):
        return plan_label(self.caps())


# ---------------------------------------------------------------- fetch: plan, manifest, image headers

MANIFEST_COLUMNS = ('file', 'kind', 'url', 'sha256', 'size', 'width', 'height', 'plan', 'original', 'fetched_at',
                    'collection_id', 'item_id')
RECORD_KINDS = ('record-html', 'record-text', 'record-json', 'normalized', 'citation', 'collection')
NOT_ORIGINAL = {'thumbnail', 'screenshot-fallback'}
RAWSTORE_KINDS = {'record-html', 'image', 'oldnews-page', 'oldnews-ocr-pdf', 'thumbnail', 'screenshot-fallback'}


def plan_fetch(caps, has_images=False, oldnews=False, thumbnail=False):
    """The kinds `fetch` tries, in order, for one record on this plan. The image is tried on every record of
    a collection with images once the plan is known to open records; on basic or unknown, the thumbnail
    stands in and is marked not original."""
    kinds = list(RECORD_KINDS)
    if caps.get('records') and has_images:
        kinds.append('image')
    if caps.get('oldnews_pages') and oldnews:
        kinds += ['oldnews-page', 'oldnews-ocr']
    if thumbnail:
        kinds.append('thumbnail')
    return kinds


_NOT_IMAGE_LINK = re.compile(r'/(?:paywall|app|apps|mobile|download-app|downloads?/?$|family-tree-builder|dna)\b|'
                             r'subscription|\.(?:exe|dmg|apk|ged|zip)(?:\?|$)', re.I)


def image_download_links(hrefs, limit=2):
    """The record-image download links among a record page's hrefs: on MyHeritage's image CDN, or naming a
    record image, never an app, paywall, GEDCOM or installer link. At most `limit`, since each one is a
    gated fetch."""
    out = []
    for h in hrefs or []:
        p = urllib.parse.urlsplit(h)
        if p.scheme not in ('http', 'https') or _NOT_IMAGE_LINK.search(p.path + '?' + p.query):
            continue
        host = p.netloc.lower()
        if host.endswith('myheritageimages.com') or re.search(r'record|image|scan', p.path + p.query, re.I):
            if h not in out:
                out.append(h)
        if len(out) >= limit:
            break
    return out


def image_size(data):
    """(width, height) from a PNG, JPEG, GIF or WebP header, else (None, None)."""
    if data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) >= 24:
        return struct.unpack('>II', data[16:24])
    if data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 10:
        return struct.unpack('<HH', data[6:10])
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP' and data[12:16] == b'VP8 ' and len(data) >= 30:
        w, h = struct.unpack('<HH', data[26:30])
        return w & 0x3fff, h & 0x3fff
    if data[:2] == b'\xff\xd8':
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xff:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xd8, 0x01) or 0xd0 <= marker <= 0xd7:
                i += 2
                continue
            seg = struct.unpack('>H', data[i + 2:i + 4])[0]
            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                h, w = struct.unpack('>HH', data[i + 5:i + 9])
                return w, h
            i += 2 + seg
    return None, None


def manifest_row(path, kind, url, plan, collection_id, item_id, fetched_at=None, original=None):
    with open(path, 'rb') as f:
        data = f.read()
    w, h = image_size(data) if kind in ('image', 'oldnews-page', 'thumbnail', 'screenshot-fallback') else (None, None)
    if original is None:
        original = kind not in NOT_ORIGINAL
    return {'file': os.path.basename(path), 'kind': kind, 'url': url or '', 'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data), 'width': w or '', 'height': h or '', 'plan': plan, 'original': 'yes' if original else 'no',
            'fetched_at': fetched_at or now_iso(), 'collection_id': collection_id, 'item_id': item_id}


def read_manifest(path):
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    return [dict(zip(MANIFEST_COLUMNS, l.split('\t'))) for l in lines[1:] if l]


def write_manifest(path, rows):
    """Merge by file name (a re-fetch replaces its row) and write sorted, so the file diffs cleanly."""
    by_file = {r['file']: r for r in read_manifest(path)}
    for r in rows:
        by_file[r['file']] = r
    out = ['\t'.join(MANIFEST_COLUMNS)]
    for name in sorted(by_file):
        out.append('\t'.join(re.sub(r'[\t\r\n]+', ' ', str(by_file[name].get(c, ''))) for c in MANIFEST_COLUMNS))
    write_atomic(path, '\n'.join(out) + '\n')


# ---------------------------------------------------------------- FamilySearch-mirror test


def _same_name(a, b):
    wa, wb = set(norm_words(a)) - {'de', 'del', 'la', 'y'}, set(norm_words(b)) - {'de', 'del', 'la', 'y'}
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


def _place_agrees(a, b):
    ta, tb = norm_words(a), norm_words(b)
    return bool(ta) and bool(tb) and (ta[0] in tb or tb[0] in ta)


def compare_mirror(fs, cands):
    """(found, agree, compared fields) for one FamilySearch record against MyHeritage candidates.
    Only fields both sides show are compared: on a basic plan a masked place or parent is skipped."""
    year, event = fs.get('year'), fs.get('event', 'birth')
    best = None
    for c in cands:
        if not _same_name(fs.get('name', ''), c.get('name', '')):
            continue
        e = (c.get('events') or {}).get(event) or {}
        if year and e.get('year') and abs(int(e['year']) - int(year)) > 1:
            continue
        best = c
        break
    if best is None:
        return False, False, []
    compared, agree = ['name'], True
    e = (best.get('events') or {}).get(event) or {}
    masked = set((best.get('mh') or {}).get('masked') or [])
    if year and e.get('year'):
        compared.append('year')
        agree &= int(e['year']) == int(year)
    if fs.get('place') and e.get('place') and '%s.place' % event not in masked:
        compared.append('place')
        agree &= _place_agrees(fs['place'], e['place'])
    rel = best.get('relatives') or []
    for role in ('father', 'mother'):
        if fs.get(role):
            names = [r['name'] for r in rel if r.get('role') in (role, 'parent')]
            if names:
                compared.append(role)
                agree &= any(_same_name(fs[role], n) for n in names)
    return True, bool(agree), compared


def mirror_verdict(n, found, agree):
    """equivalent: at least 90% found and every found one agrees; partial: at least half found; else not."""
    if n and found >= math.ceil(0.9 * n) and agree == found:
        return 'equivalent'
    if n and found >= math.ceil(0.5 * n):
        return 'partial'
    return 'not'


MIRROR_COLUMNS = ('cid', 'fs_collection', 'n', 'found', 'agree', 'date', 'verdict', 'plan', 'compared')


# ---------------------------------------------------------------- output names


def search_key(url, given='', surnames=(), by=None):
    h = hashlib.sha256(normalize_url(url).encode()).hexdigest()[:10]
    return '%s-%s' % (slug(' '.join([given] + list(surnames) + ([str(by)] if by else []))), h)
