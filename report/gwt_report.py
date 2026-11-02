#!/usr/bin/env python3
"""gwt-report: how two people of a tree are related, and what is new in a tree since the last time you told someone.

Reads offline files (a `gwt-check dump` or a `gwt-apply` snapshot, gzipped or not) and never touches the tree.

  gwt-report kin [--lang en|es|fr] [--json] [--weak-tag TEXT] DUMP A B
  gwt-report digest --from OLD --to NEW [--root I0001] [--base-url URL] [--told FILE --audience NAME [--mark]]
                    [--format md|html]

kin lists every blood path between A and B (through parent families where the link is Birth, Adopted, Unknown or
empty; never through a marriage), the nearest common ancestors with their Ahnentafel numbers seen from A and from
B, the relationship name in English, Spanish and French, and how many links of each path are confirmed, probable,
weak-tagged or unmarked. It also lists implex: one ancestor (or couple) reached at two or more Ahnentafel numbers
in A's or B's pedigree.

digest lists the people, events, citations and media in NEW that OLD lacks, grouped by person and, with --root,
by side of the root's family and ordered by kin distance. Each item gets its Gramps Web link (--base-url) and the
web addresses its citations hold. --told FILE (TSV: audience, object id, date) leaves out what that audience was
already told; --mark appends what this digest shows. Exit 2 on a bad file. Standard library only.
"""
import argparse, collections, datetime, gzip, html, json, os, re, sys, unicodedata

CLASSES = {'Person': 'people', 'Family': 'families', 'Event': 'events', 'Citation': 'citations', 'Media': 'media',
           'Note': 'notes', 'Tag': 'tags', 'Place': 'places', 'Source': 'sources'}
SNAPSHOT_FORMAT = 'gwt-apply-snapshot/1'
ACCEPTED = 'a gwt-check dump or a gwt-apply snapshot'
LINEAGE = ('', 'Birth', 'Unknown', 'Adopted')  # a parent link kin follows; Stepchild, Foster, Sponsored are not blood
WEAK_TAGS = ('hipotesis', 'hypothes', 'weak')  # a tag whose folded name holds one of these marks a weak link
PROBABLE, CONFIRMED = '[probable]', '[confirmed]'  # the marks gwt-apply puts on a child_ref note
LINK_CLASSES = ('confirmed', 'probable', 'weak', 'unmarked')
MAX_GEN = 30         # how far up kin and implex look
MAX_PATHS = 2000     # paths kept per person, nearest first: a big pedigree or a deep implex multiplies them
MAX_PEDIGREE = 200000
WEB_KIND = {'people': 'person', 'events': 'event', 'citations': 'citation', 'media': 'media'}
URL_RE = re.compile(r'https?://[^\s<>"\]\)]+')


class BadInput(Exception):
    pass


def load(path):
    """Every class kin and digest read, keyed by handle. A file in another shape is an error, never an empty tree."""
    try:
        with open(path, 'rb') as f:
            gz = f.read(2) == b'\x1f\x8b'
        with (gzip.open if gz else open)(path, 'rt', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError, EOFError) as exc:
        raise BadInput('%s: not JSON, so not %s (%s)' % (path, ACCEPTED, exc))
    if not isinstance(data, dict):
        raise BadInput('%s: not %s' % (path, ACCEPTED))
    meta = data
    snapshot = data.get('format') == SNAPSHOT_FORMAT
    data = data.get('objects', data)
    if not isinstance(data, dict):
        raise BadInput('%s: not %s: expected one list per type' % (path, ACCEPTED))
    if snapshot:
        data = {CLASSES[k]: v for k, v in data.items() if k in CLASSES}
    for t in ('people', 'families'):
        if not isinstance(data.get(t), list):
            raise BadInput('%s: no %s list: is this %s?' % (path, t, ACCEPTED))
    if not data['people']:
        raise BadInput('%s: the file holds no people' % path)
    T = {}
    for t in CLASSES.values():
        objs = data.get(t) or []
        if not isinstance(objs, list) or any(not isinstance(o, dict) or 'handle' not in o for o in objs):
            raise BadInput('%s: %s must be a list of objects with a handle' % (path, t))
        T[t] = {o['handle']: o for o in objs}
    T['_taken'] = meta.get('taken') or meta.get('dumped_at') or ''
    T['_by_id'] = {}
    for t in WEB_KIND:
        for o in T[t].values():
            if o.get('gramps_id'):
                T['_by_id'].setdefault(o['gramps_id'], (t, o['handle']))
    return T


def text_of(value):
    return (value.get('string') if isinstance(value, dict) else value) or ''


def fold(s):
    s = unicodedata.normalize('NFKD', s or '').casefold()
    return ''.join(c for c in s if not unicodedata.combining(c))


def label(p):
    n = p.get('primary_name') or {}
    sn = ' '.join(s.get('surname', '') for s in n.get('surname_list', []) if s.get('surname'))
    return ' '.join(x for x in (n.get('first_name', ''), sn) if x) or p.get('gramps_id', '') or '?'


def who(T, h):
    p = T['people'][h]
    return '%s (%s)' % (label(p), p.get('gramps_id') or h)


def person_handle(T, ref):
    if ref in T['people']:
        return ref
    t, h = T['_by_id'].get(ref, (None, None))
    if t != 'people':
        raise BadInput('no person %s in the file' % ref)
    return h


# ---- kin ---------------------------------------------------------------------------------------------------------

def parent_steps(T, h):
    """(parent, 'father'|'mother', family, child_ref) for each parent link of h that carries blood or adoption."""
    for fh in T['people'][h].get('parent_family_list') or []:
        f = T['families'].get(fh)
        if not f:
            continue
        ref = next((c for c in f.get('child_ref_list') or [] if c.get('ref') == h), None)
        if ref is None:
            continue
        for key, role, rel in (('father_handle', 'father', 'frel'), ('mother_handle', 'mother', 'mrel')):
            if f.get(key) in T['people'] and text_of(ref.get(rel)) in LINEAGE:
                yield f[key], role, fh, ref


def ancestor_paths(T, h, max_gen=MAX_GEN, max_paths=MAX_PATHS):
    """{ancestor: [path, ...]}, h itself with the empty path. A path is a tuple of steps (child, parent, role, family,
    child_ref) from h upwards. The walk goes one generation at a time, so the cap drops the most distant paths
    first, never a whole side. Returns (paths, truncated)."""
    out = {h: [()]}
    count = 0
    todo = collections.deque([(h, (), frozenset((h,)))])
    while todo:
        cur, path, seen = todo.popleft()
        if len(path) >= max_gen:
            continue
        for parent, role, fh, ref in parent_steps(T, cur):
            if parent in seen:
                continue  # a person recorded as their own ancestor: bad data, never an endless walk
            if count >= max_paths:
                return out, True
            count += 1
            p2 = path + ((cur, parent, role, fh, ref),)
            out.setdefault(parent, []).append(p2)
            todo.append((parent, p2, seen | {parent}))
    return out, False


def ahnentafel(path):
    n = 1
    for step in path:
        n = 2 * n + (step[2] == 'mother')
    return n


def tagged_weak(T, obj, weak_tags):
    return any(any(w in fold(text_of((T['tags'].get(t) or {}).get('name'))) for w in weak_tags)
               for t in obj.get('tag_list') or [])


def link_class(T, step, weak_tags):
    """confirmed, probable, weak or unmarked, for one child -> parent link."""
    child, _, _, fh, ref = step
    if tagged_weak(T, T['families'][fh], weak_tags) or tagged_weak(T, T['people'][child], weak_tags):
        return 'weak'
    marks = [text_of((T['notes'].get(n) or {}).get('text')).lstrip() for n in ref.get('note_list') or []]
    if any(m.startswith(PROBABLE) for m in marks):
        return 'probable'
    if any(m.startswith(CONFIRMED) for m in marks) or any(
            ((T['citations'].get(c) or {}).get('confidence') or 0) >= 3 for c in ref.get('citation_list') or []):
        return 'confirmed'
    return 'unmarked'


def relationships(T, a, b, weak_tags=WEAK_TAGS, max_paths=MAX_PATHS, a_paths=None):
    """Every blood relationship between a and b, nearest first. Each is one line of descent: the nearest common
    ancestor (or couple), the path from a and from b, generations up from each, half or not. a_paths is
    ancestor_paths(T, a) when the caller already has it."""
    pa, ta = a_paths or ancestor_paths(T, a, max_paths=max_paths)
    pb, tb = ancestor_paths(T, b, max_paths=max_paths)
    common = set(pa) & set(pb)
    groups = {}
    for c in common:
        for path_a in pa[c]:
            on_a = {s[0] for s in path_a}
            for path_b in pb[c]:
                # two lines that meet below c (share a person) belong to that nearer ancestor's relationship
                if not on_a.isdisjoint(s[0] for s in path_b):
                    continue
                key = (tuple(s[0] for s in path_a), tuple(s[0] for s in path_b),
                       path_a[-1][3] if path_a else None, path_b[-1][3] if path_b else None)
                groups.setdefault(key, []).append((c, path_a, path_b))
    rels = []
    for (_, _, fam_a, fam_b), members in groups.items():
        members.sort(key=lambda m: ahnentafel(m[1]))
        _, path_a, path_b = members[0]
        g1, g2 = len(path_a), len(path_b)
        steps = list(path_a) + list(path_b)
        links = dict.fromkeys(LINK_CLASSES, 0)
        for s in steps:
            links[link_class(T, s, weak_tags)] += 1
        rels.append({
            'generations': [g1, g2],
            'half': bool(g1 and g2) and (fam_a != fam_b or len(members) == 1 and all(
                T['families'][fam_a].get(k) in T['people'] for k in ('father_handle', 'mother_handle'))),
            'adopted': any(text_of(s[4].get('frel' if s[2] == 'father' else 'mrel')) == 'Adopted'
                           for m in members for s in m[1] + m[2]),
            'ancestors': [(c, ahnentafel(p1), ahnentafel(p2)) for c, p1, p2 in members],
            'path_a': [a] + [s[1] for s in path_a],
            'path_b': [b] + [s[1] for s in path_b],
            'links': links,
            'side': path_a[0][2] if path_a else None,
        })
    rels.sort(key=lambda r: (sum(r['generations']), r['ancestors'][0][1]))
    return rels, ta or tb


def gender(T, h):
    g = T['people'][h].get('gender')
    return g if g in (0, 1) else None


EN_ORD = ('', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth')
ES_ORD = ('', '', 'segund', 'tercer', 'cuart', 'quint', 'sext', 'séptim', 'octav', 'noven', 'décim')


def name_en(g1, g2, half, sex):
    def pick(m, f, n):
        return m if sex == 1 else f if sex == 0 else n

    def greats(g, word):  # g >= 2 generations: grand-, great-grand-, great-great-grand-, 3x great-grand-
        if g == 2:
            return 'grand' + word
        return ('great-' * (g - 2) if g <= 4 else '%dx great-' % (g - 2)) + 'grand' + word
    if g1 == 0 and g2 == 0:
        return 'same person'
    if g1 == 0:
        w = pick('son', 'daughter', 'child')
        return w if g2 == 1 else greats(g2, w)
    if g2 == 0:
        w = pick('father', 'mother', 'parent')
        return w if g1 == 1 else greats(g1, w)
    h = 'half ' if half else ''
    if g1 == 1 and g2 == 1:
        return h.replace(' ', '-') + pick('brother', 'sister', 'sibling')
    if g1 == 1 or g2 == 1:
        w = pick('nephew', 'niece', 'nephew or niece') if g1 == 1 else pick('uncle', 'aunt', 'uncle or aunt')
        g = max(g1, g2)
        pre = '' if g == 2 else 'great-' * (g - 2) if g <= 4 else '%dx great-' % (g - 2)
        return h + pre + w
    degree, removed = min(g1, g2) - 1, abs(g1 - g2)
    out = (EN_ORD[degree] if degree < len(EN_ORD) else '%dth' % degree) + ' cousin'
    if removed:
        out += ' ' + {1: 'once', 2: 'twice'}.get(removed, '%d times' % removed) + ' removed'
    return ('half ' if half else '') + out


def name_es(g1, g2, half, sex):
    fem = sex == 0

    def g(word):  # masculine word ending in -o made feminine
        return word[:-1] + 'a' if fem and word.endswith('o') else word
    up = ('', 'padre', 'abuelo', 'bisabuelo', 'tatarabuelo')
    down = ('', 'hijo', 'nieto', 'bisnieto', 'tataranieto')
    if g1 == 0 and g2 == 0:
        return 'la misma persona'
    if g1 == 0:
        return g(down[g2]) if g2 < len(down) else 'descendiente en %d generaciones' % g2
    if g2 == 0:
        if g1 == 1:
            return 'madre' if fem else 'padre'
        return g(up[g1]) if g1 < len(up) else ('antepasada' if fem else 'antepasado') + ' en %d generaciones' % g1
    half_word = ('media ' if fem else 'medio ') if half else ''
    n, d = min(g1, g2), abs(g1 - g2)
    ordinal = '' if n == 1 else ' ' + (ES_ORD[n] + ('a' if fem else 'o') if n < len(ES_ORD) else 'de grado %d' % n)
    if d == 0:
        if n == 1:
            return half_word + ('hermana' if fem else 'hermano')
        cousin = 'prima' if fem else 'primo'
        if n == 2:
            return half_word + cousin + (' hermana' if fem else ' hermano')
        return half_word + cousin + ' ' + (ES_ORD[n - 1] + ('a' if fem else 'o') if n - 1 < len(ES_ORD) else 'de grado %d' % (n - 1))
    if g1 < g2:  # b is in a lower generation: sobrino, sobrino nieto, sobrino bisnieto
        base = ('sobrina' if fem else 'sobrino') + ('' if d == 1 else ' ' + g(down[d]) if d < len(down) else ' en %d generaciones' % d)
    else:
        base = ('tía' if fem else 'tío') + ('' if d == 1 else ' ' + g(up[d]) if d < len(up) else ' en %d generaciones' % d)
    return half_word + base + ordinal


def name_fr(g1, g2, half, sex):
    def pick(m, f, n=None):
        return f if sex == 0 else m if sex == 1 or n is None else n
    if g1 == 0 and g2 == 0:
        return 'la même personne'
    if g1 == 0 or g2 == 0:
        g = max(g1, g2)
        if g == 1:
            return pick('fils', 'fille', 'enfant') if g1 == 0 else pick('père', 'mère', 'parent')
        w = pick('petit-fils', 'petite-fille', 'petit-enfant') if g1 == 0 else pick('grand-père', 'grand-mère', 'grand-parent')
        if g > 4:
            return (pick('descendant', 'descendante') if g1 == 0 else 'ancêtre') + ' à la %de génération' % g
        return 'arrière-' * (g - 2) + w
    demi = 'demi-' if half else ''
    if g1 == 1 and g2 == 1:
        return demi + pick('frère', 'sœur')
    if g1 == 1 or g2 == 1:
        g = max(g1, g2)
        if g1 == 1:
            w = pick('neveu', 'nièce') if g == 2 else 'arrière-' * (g - 3) + pick('petit-neveu', 'petite-nièce')
        else:
            w = pick('oncle', 'tante') if g == 2 else 'arrière-' * (g - 3) + pick('grand-oncle', 'grand-tante')
        return demi + w
    deg, gap = min(g1, g2) - 1, abs(g1 - g2)
    words = {1: pick('cousin germain', 'cousine germaine'), 2: pick('cousin issu de germain', 'cousine issue de germain'),
             3: pick("cousin issu d'issu de germain", "cousine issue d'issue de germain")}
    out = demi + words.get(deg, pick('cousin', 'cousine') + ' au %de degré de cousinage' % deg)
    if gap:
        gens = {1: 'une génération', 2: 'deux générations'}.get(gap, '%d générations' % gap)
        out += ', %s %s' % (gens, 'au-dessous' if g2 > g1 else 'au-dessus')
    return out


NAMERS = {'en': name_en, 'es': name_es, 'fr': name_fr}


def names(g1, g2, half, sex):
    return {lang: f(g1, g2, half, sex) for lang, f in NAMERS.items()}


def pedigree(T, h, max_gen=MAX_GEN):
    """{person: [Ahnentafel numbers]} over the first father and first mother of each person, and truncated."""
    nums, todo, n_seen = {}, [(h, 1, 0)], 0
    while todo:
        cur, n, gen = todo.pop()
        nums.setdefault(cur, []).append(n)
        n_seen += 1
        if gen >= max_gen or n_seen >= MAX_PEDIGREE:
            continue
        got = set()
        for parent, role, _, _ in parent_steps(T, cur):
            if role not in got:
                got.add(role)
                todo.append((parent, 2 * n + (role == 'mother'), gen + 1))
    return {p: sorted(ns) for p, ns in nums.items()}, n_seen >= MAX_PEDIGREE


def implex(T, h):
    """The lowest ancestors reached at more than one Ahnentafel number: couples (father at n, mother at n+1 every
    time) and single people. An ancestor whose every number has the same child below is the parent of an implex
    already listed, so it is left out."""
    nums, truncated = pedigree(T, h)
    at = {n: p for p, ns in nums.items() for n in ns}
    out, done = [], set()
    for p, ns in sorted(nums.items(), key=lambda kv: kv[1][0]):
        if len(ns) < 2 or p in done or len({at.get(n // 2) for n in ns}) < 2:
            continue
        mates = {at.get(n + 1) for n in ns} if all(n % 2 == 0 for n in ns) else {None}
        mate = next(iter(mates)) if len(mates) == 1 else None
        if mate and nums.get(mate) and len(nums[mate]) == len(ns):
            out.append({'people': [p, mate], 'numbers': [[n, n + 1] for n in ns]})
            done |= {p, mate}
        else:
            out.append({'people': [p], 'numbers': [[n] for n in ns]})
            done.add(p)
    return out, truncated


def kin(T, a, b, weak_tags=WEAK_TAGS):
    rels, truncated = relationships(T, a, b, weak_tags)
    sex = gender(T, b)
    for r in rels:
        r['name'] = names(r['generations'][0], r['generations'][1], r['half'], sex)
    return {'a': a, 'b': b, 'relationships': rels, 'truncated': truncated,
            'implex': {'a': implex(T, a)[0], 'b': implex(T, b)[0] if b != a else []}}


def kin_json(T, rep):
    gid = lambda h: T['people'][h].get('gramps_id') or h
    return {
        'a': gid(rep['a']), 'b': gid(rep['b']), 'truncated': rep['truncated'],
        'relationships': [{
            'name': r['name'], 'generations': r['generations'], 'half': r['half'], 'adopted': r['adopted'],
            'ancestors': [{'id': gid(c), 'name': label(T['people'][c]), 'ahnentafel_a': na, 'ahnentafel_b': nb}
                          for c, na, nb in r['ancestors']],
            'path_a': [gid(h) for h in r['path_a']], 'path_b': [gid(h) for h in r['path_b']], 'links': r['links'],
        } for r in rep['relationships']],
        'implex': {k: [{'ids': [gid(h) for h in e['people']], 'numbers': e['numbers']} for e in v]
                   for k, v in rep['implex'].items()},
    }


def print_kin(T, rep, lang):
    a, b = rep['a'], rep['b']
    print('%s -> %s' % (who(T, a), who(T, b)))
    if not rep['relationships']:
        print('  no blood path')
    for r in rep['relationships']:
        print('  %s%s' % (r['name'][lang], ' (through an adoption)' if r['adopted'] else ''))
        anc = ' and '.join(who(T, c) for c, _, _ in r['ancestors'])
        print('    common ancestors: %s; Ahnentafel from A %s, from B %s' % (
            anc, '-'.join(str(x[1]) for x in r['ancestors']), '-'.join(str(x[2]) for x in r['ancestors'])))
        print('    path from A: %s' % ' > '.join(T['people'][h].get('gramps_id') or h for h in r['path_a']))
        print('    path from B: %s' % ' > '.join(T['people'][h].get('gramps_id') or h for h in r['path_b']))
        print('    links: ' + ', '.join('%d %s' % (r['links'][k], k) for k in LINK_CLASSES))
    for side, h in (('A', a), ('B', b)):
        for e in rep['implex'][side.lower()]:
            print('  implex in %s\'s pedigree: %s at %s' % (
                side, ' and '.join(who(T, p) for p in e['people']), ', '.join('-'.join(map(str, ns)) for ns in e['numbers'])))
    if rep['truncated']:
        print('  (path list cut at %d paths per person: the most distant relationships are not shown)' % MAX_PATHS)


# ---- digest ------------------------------------------------------------------------------------------------------

def refs_in(obj, key):
    """Every handle under any `key` list in obj, at any depth: citation_list holds handles, media_list refs."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, list):
                out += [x.get('ref') if isinstance(x, dict) else x for x in v]
            else:
                out += refs_in(v, key)
    elif isinstance(obj, list):
        for v in obj:
            out += refs_in(v, key)
    return [x for x in out if x]


def holders(T):
    """{handle of an event, citation or media: set of people it belongs to}."""
    own = {}

    def add(h, people):
        own.setdefault(h, set()).update(p for p in people if p in T['people'])
    for p in T['people'].values():
        for r in p.get('event_ref_list') or []:
            add(r.get('ref'), [p['handle']])
        for k in ('citation_list', 'media_list'):
            for h in refs_in(p, k):
                add(h, [p['handle']])
    for f in T['families'].values():
        spouses = [f.get('father_handle'), f.get('mother_handle')]
        for r in f.get('event_ref_list') or []:
            add(r.get('ref'), spouses)
        for c in f.get('child_ref_list') or []:
            for h in refs_in(c, 'citation_list'):
                add(h, [c.get('ref')])
        rest = {k: v for k, v in f.items() if k != 'child_ref_list'}
        for k in ('citation_list', 'media_list'):
            for h in refs_in(rest, k):
                add(h, spouses)
    for t in ('events', 'citations', 'media'):  # what an event or a citation carries belongs to its people
        for o in T[t].values():
            for k in ('citation_list', 'media_list'):
                for h in refs_in(o, k):
                    if h != o['handle']:
                        own.setdefault(h, set()).update(own.get(o['handle'], set()))
    return own


def urls_of(T, citation):
    c = T['citations'].get(citation) or {}
    texts = [c.get('page') or ''] + [text_of(a.get('value')) for a in c.get('attribute_list') or []]
    texts += [text_of((T['notes'].get(n) or {}).get('text')) for n in c.get('note_list') or []]
    out = []
    for t in texts:
        for u in URL_RE.findall(t):
            u = u.rstrip('.,;')
            if u not in out:
                out.append(u)
    return out


def year_of(ev):
    d = (ev or {}).get('date') or {}
    v = d.get('dateval') or []
    return d.get('year') or (v[2] if len(v) > 2 and isinstance(v[2], int) else 0) or None


def describe(T, t, o):
    if t == 'people':
        birth = None
        refs = o.get('event_ref_list') or []
        i = o.get('birth_ref_index', -1)
        if isinstance(i, int) and 0 <= i < len(refs):
            birth = T['events'].get(refs[i].get('ref'))
        y = year_of(birth)
        return 'new person: %s%s' % (label(o), ', born %s' % y if y else '')
    if t == 'events':
        name = (T['places'].get(o.get('place')) or {}).get('name')
        place = text_of(name.get('value')) if isinstance(name, dict) else ''
        bits = [text_of(o.get('type')) or 'event', str(year_of(o) or ''), place, o.get('description') or '']
        return 'new event: ' + ', '.join(b for b in bits if b)
    if t == 'citations':
        src = text_of((T['sources'].get(o.get('source_handle')) or {}).get('title'))
        bits = [src, o.get('page') or '', 'confidence %s' % o.get('confidence', '?')]
        return 'new citation: ' + ', '.join(b for b in bits if b)
    return 'new media: ' + (o.get('desc') or o.get('path') or o.get('gramps_id') or '')


def is_weak(T, t, o, weak_tags):
    return tagged_weak(T, o, weak_tags) or (t == 'citations' and (o.get('confidence') or 0) <= 1)


def read_told(path, audience):
    """The object ids this audience was already told. A missing file is nobody told anything yet."""
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding='utf-8-sig') as fh:
            lines = fh.readlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BadInput('%s: cannot read the told file (%s)' % (path, exc))
    told = set()
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.startswith('#'):
            continue
        cells = [c.strip() for c in line.rstrip('\n').split('\t')]
        if n == 1 and cells[0].lower() == 'audience':
            continue
        if len(cells) < 2 or not cells[0] or not cells[1]:
            raise BadInput('%s line %d: expected audience<TAB>object id<TAB>date' % (path, n))
        if cells[0] == audience:
            told.add(cells[1])
    return told


def digest(old, new, root=None, told=frozenset(), weak_tags=WEAK_TAGS, include_private=False):
    """Groups of new items: [{side, person, kin, distance, items: [...]}], nearest kin first."""
    own = holders(new)
    by_person = {}
    for t in WEB_KIND:
        for h, o in new[t].items():
            if h in old[t] or (o.get('private') and not include_private):
                continue
            gid = o.get('gramps_id') or h
            if gid in told:
                continue
            people = {h} if t == 'people' else own.get(h, set()) or {None}
            item = {'type': t, 'id': gid, 'text': describe(new, t, o), 'weak': is_weak(new, t, o, weak_tags),
                    'urls': urls_of(new, h) if t == 'citations' else
                    [u for c in o.get('citation_list') or [] for u in urls_of(new, c)]}
            for p in people:
                if p and new['people'][p].get('private') and not include_private:
                    continue
                by_person.setdefault(p, []).append(item)
    groups = []
    root_paths = ancestor_paths(new, root) if root else None
    for p, items in by_person.items():
        g = {'person': p, 'items': items, 'side': 'other', 'kin': None, 'distance': None, 'links': None,
             'truncated': False}
        if p and root:
            if p == root:
                g.update(side='root', distance=0, kin=names(0, 0, False, None))
            else:
                rels, g['truncated'] = relationships(new, root, p, weak_tags, a_paths=root_paths)
                if rels:
                    r = rels[0]
                    roles = {rr['side'] for rr in rels}
                    side = 'descendants' if roles == {None} else 'both sides' if {'father', 'mother'} <= roles \
                        else next(x for x in roles if x) + "'s side"
                    g.update(side=side, distance=sum(r['generations']), links=r['links'],
                             kin=names(r['generations'][0], r['generations'][1], r['half'], gender(new, p)))
                else:
                    g['side'] = 'not blood kin'
        groups.append(g)
    order = {'root': 0, "father's side": 1, "mother's side": 2, 'both sides': 3, 'descendants': 4,
             'not blood kin': 5, 'other': 6}

    def key(g):
        p = g['person']
        return (order[g['side']], g['distance'] if g['distance'] is not None else 1 << 30,
                (new['people'][p].get('gramps_id') or p) if p else '~')
    groups.sort(key=key)
    return groups


def web_url(base, t, gid):
    return '%s/%s/%s' % (base.rstrip('/'), WEB_KIND[t], gid) if base else ''


def render(T, groups, base, lang, fmt, since=''):
    lines = []
    esc = html.escape if fmt == 'html' else (lambda s: s)
    side = None
    title = 'New in the tree' + (' since %s' % since if since else '')
    lines.append('<html><body><h1>%s</h1>' % esc(title) if fmt == 'html' else '# ' + title)
    if not groups:
        lines.append('<p>Nothing new.</p>' if fmt == 'html' else '\nNothing new.')
    for g in groups:
        if g['side'] != side:
            side = g['side']
            lines.append('<h2>%s</h2>' % esc(side) if fmt == 'html' else '\n## %s' % side)
        p = g['person']
        if p:
            head = who(T, p)
            if g['kin']:
                head += ', ' + g['kin'][lang]
            url = web_url(base, 'people', T['people'][p].get('gramps_id') or p)
            if g['links'] and (g['links']['probable'] or g['links']['weak']):
                head += ' (path: %d confirmed, %d probable, %d weak, %d unmarked links)' % tuple(
                    g['links'][k] for k in LINK_CLASSES)
        else:
            head, url = 'not attached to a person', ''
        if fmt == 'html':
            lines.append('<h3>%s%s</h3><ul>' % (esc(head), ' <a href="%s">%s</a>' % (esc(url), esc(url)) if url else ''))
        else:
            lines.append('\n### %s%s\n' % (head, ' ' + url if url else ''))
        for it in g['items']:
            link = web_url(base, it['type'], it['id'])
            text = it['text'] + (' [weak]' if it['weak'] else '')
            if fmt == 'html':
                bits = [esc(text)] + ['<a href="%s">%s</a>' % (esc(u), esc(u)) for u in [link] + it['urls'] if u]
                lines.append('<li>%s</li>' % ' '.join(bits))
            else:
                lines.append('- ' + ' '.join([text] + [u for u in [link] + it['urls'] if u]))
        if fmt == 'html':
            lines.append('</ul>')
    cut = sum(1 for g in groups if g.get('truncated'))
    if cut:
        note = ('kin paths were cut at %d per person for %d of these people: a more distant relationship, or the '
                'only one, may be missing' % (MAX_PATHS, cut))
        lines.append('<p>%s</p>' % esc(note) if fmt == 'html' else '\n(%s)' % note)
    if fmt == 'html':
        lines.append('</body></html>')
    return '\n'.join(lines).lstrip('\n') + '\n'


def mark(path, audience, ids):
    today = datetime.date.today().isoformat()
    try:
        with open(path, 'ab+') as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                head = 'audience\tobject\tdate\n'
            else:
                fh.seek(-1, os.SEEK_END)
                head = '' if fh.read(1) == b'\n' else '\n'  # a hand-edited file may lack its last newline
            fh.write((head + ''.join('%s\t%s\t%s\n' % (audience, i, today) for i in ids)).encode('utf-8'))
    except OSError as exc:
        raise BadInput('%s: cannot write the told file (%s)' % (path, exc))


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-report', description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('kin', help='every blood path between two people, named in en, es and fr, and implex')
    k.add_argument('dump')
    k.add_argument('a', help='Gramps id of the first person')
    k.add_argument('b', help='Gramps id of the second person')
    k.add_argument('--lang', choices=sorted(NAMERS), default='en', help='language of the text output (json has all)')
    k.add_argument('--json', action='store_true')
    k.add_argument('--weak-tag', action='append', metavar='TEXT',
                   help='a tag whose name holds TEXT (accents and case ignored) marks a weak link; repeat for more '
                        '(default: %s)' % ', '.join(WEAK_TAGS))
    d = sub.add_parser('digest', help='what is new in a tree since an older snapshot, by person, with links')
    d.add_argument('--from', dest='old', required=True, metavar='OLD')
    d.add_argument('--to', dest='new', required=True, metavar='NEW')
    d.add_argument('--root', help='Gramps id: order people by kin distance to this person and group by side')
    d.add_argument('--base-url', default='', help='the Gramps Web address, for a link to each person and item')
    d.add_argument('--lang', choices=sorted(NAMERS), default='en')
    d.add_argument('--told', metavar='FILE', help='TSV of audience, object id, date: leave out what was told')
    d.add_argument('--audience', help='with --told: whose told rows count')
    d.add_argument('--mark', action='store_true', help='append every item shown to --told for --audience')
    d.add_argument('--format', choices=('md', 'html'), default='md')
    d.add_argument('--weak-tag', action='append', metavar='TEXT')
    d.add_argument('--include-private', action='store_true', help='also list objects and people marked private')
    args = p.parse_args(argv)
    weak = tuple(fold(w) for w in args.weak_tag) if args.weak_tag else WEAK_TAGS
    try:
        if args.cmd == 'kin':
            T = load(args.dump)
            rep = kin(T, person_handle(T, args.a), person_handle(T, args.b), weak)
            if args.json:
                print(json.dumps(kin_json(T, rep), ensure_ascii=False, indent=1))
            else:
                print_kin(T, rep, args.lang)
            return 0
        if (args.told or args.mark) and not args.audience:
            p.error('--told and --mark need --audience')
        if (args.audience or args.mark) and not args.told:
            p.error('--audience and --mark need --told FILE')
        old, new = load(args.old), load(args.new)
        root = person_handle(new, args.root) if args.root else None
        told = read_told(args.told, args.audience) if args.told else frozenset()
        groups = digest(old, new, root, told, weak, args.include_private)
        sys.stdout.write(render(new, groups, args.base_url, args.lang, args.format, old['_taken']))
        if args.mark:
            ids = []
            for g in groups:
                ids += [it['id'] for it in g['items'] if it['id'] not in ids]
            mark(args.told, args.audience, ids)
            print('gwt-report: marked %d items as told to %s' % (len(ids), args.audience), file=sys.stderr)
        return 0
    except BadInput as exc:
        print('gwt-report: %s' % exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
