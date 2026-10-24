#!/usr/bin/env python3
"""gwt-leads: turn the gaps of a tree dump into research tasks for gwt-queue.

Reads an offline dump (the JSON `gwt-check dump` writes, .gz ok) and never touches the tree. Exit 2 on a file that
is not a dump, never a silent empty list. Prints one
task per gap, highest priority first: direct ancestors of --root count 100 minus a point per generation
(never below 40), everyone else 10.

  gwt-leads [--root I0001] [--kinds branch-end,missing-vital,uncited] [--limit N] [--format queue|tsv|json] DUMP

Gaps:
  branch-end     a person with no parents recorded: where a line stops
  missing-vital  no birth, no marriage (a family with children but no marriage event) or, for people born
                 before 1900, no death; or a vital event with no date or no place
  uncited        a vital event with no citation: a fact nobody can check

`--format queue` prints `gwt-queue add ... --dedupe` lines to pipe into sh; the person key is the Gramps id.
Standard library only.
"""
import argparse, collections, gzip, json, shlex, sys

KINDS = ('branch-end', 'missing-vital', 'uncited')
VITALS = ('Birth', 'Death', 'Marriage')
LIVING_CUTOFF = 1900  # no death is asked of someone born after this


class BadInput(Exception):
    pass


def load(path):
    """The people, families and events of a `gwt-check dump` file: {tree, dumped_at, objects: {people: [...]}}.
    A bare {people: [...]} map is read too. Anything else is an error, never an empty result."""
    opener = gzip.open if path.endswith('.gz') else open
    try:
        with opener(path, 'rt', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError, EOFError) as exc:
        raise BadInput('%s: not a JSON dump (%s)' % (path, exc))
    if isinstance(data, dict):
        data = data.get('objects', data)
    if not isinstance(data, dict):
        raise BadInput('%s: a dump is a JSON object with one list per type' % path)
    bad = [t for t in ('people', 'families', 'events') if not isinstance(data.get(t), list)]
    if bad:
        raise BadInput('%s: no %s list: is this a gwt-check dump?' % (path, ', '.join(bad)))
    if not data['people']:
        raise BadInput('%s: the dump holds no people' % path)
    return {t: {o['handle']: o for o in data[t]} for t in ('people', 'families', 'events')}


def etype(ev):
    t = ev.get('type')
    return (t.get('string') if isinstance(t, dict) else t) or ''


def year(ev):
    d = ev.get('date') or {}
    v = d.get('dateval') or []
    y = d.get('year') if isinstance(d.get('year'), int) and d.get('year') else None
    return y or (v[2] if len(v) > 2 and isinstance(v[2], int) and v[2] else None)


def date_gap(ev):
    """'' if the event has a structured year; else why it has none: no date at all, or text we cannot read as a year."""
    if year(ev):
        return ''
    text = ((ev.get('date') or {}).get('text') or '').strip()
    return 'has date text without an interpretable year (%s)' % text if text else 'has no date'


def label(p):
    n = p.get('primary_name') or {}
    sn = ' '.join(s.get('surname', '') for s in n.get('surname_list', []))
    return ' '.join(x for x in (n.get('first_name', ''), sn) if x) or p.get('gramps_id', '')


def parents(T, p):
    out = []
    for fh in p.get('parent_family_list', []):
        f = T['families'].get(fh) or {}
        out += [h for h in (f.get('father_handle'), f.get('mother_handle')) if h in T['people']]
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


def person_events(T, p):
    return [T['events'][r['ref']] for r in p.get('event_ref_list', []) if r.get('ref') in T['events']]


def leads(T, root_gid=None, kinds=KINDS):
    gen = {}
    if root_gid:
        root = next((h for h, p in T['people'].items() if p.get('gramps_id') == root_gid), None)
        if root is None:
            raise BadInput('no person %s in the dump' % root_gid)
        gen = generations(T, root)
    out = []

    def add(p, kind, what):
        g = gen.get(p['handle'])
        prio = max(40, 100 - g) if g is not None else 10
        out.append({'person': p.get('gramps_id', p['handle']), 'label': label(p), 'kind': kind,
                    'what': what, 'priority': prio, 'generation': g})

    for p in T['people'].values():
        evs = person_events(T, p)
        by = collections.defaultdict(list)
        for e in evs:
            by[etype(e)].append(e)
        if 'branch-end' in kinds and not parents(T, p):
            add(p, 'branch-end', 'no parents recorded')
        if 'missing-vital' in kinds:
            if not by['Birth']:
                add(p, 'missing-vital', 'no birth event')
            born = year(by['Birth'][0]) if by['Birth'] else None
            if not by['Death'] and born is not None and born <= LIVING_CUTOFF:
                add(p, 'missing-vital', 'no death event (born %d)' % born)
            for t in ('Birth', 'Death'):
                for e in by[t]:
                    if date_gap(e):
                        add(p, 'missing-vital', '%s %s' % (t.lower(), date_gap(e)))
                    if not e.get('place'):
                        add(p, 'missing-vital', '%s has no place' % t.lower())
        if 'uncited' in kinds:
            for t in ('Birth', 'Death'):
                for e in by[t]:
                    if not e.get('citation_list'):
                        add(p, 'uncited', '%s has no citation' % t.lower())
    if 'missing-vital' in kinds:
        for f in T['families'].values():
            if f.get('child_ref_list') and not any(
                    etype(T['events'].get(r.get('ref'), {})) == 'Marriage' for r in f.get('event_ref_list', [])):
                for h in (f.get('father_handle'), f.get('mother_handle')):
                    if h in T['people']:
                        add(T['people'][h], 'missing-vital',
                            'family %s has children but no marriage event' % f.get('gramps_id', ''))
                        break
    out.sort(key=lambda r: (-r['priority'], r['person'], r['kind'], r['what']))
    return out


def group_by_person(rows):
    """One task per person and task kind: the gaps of one person are one search, not one search per gap."""
    groups = {}
    for r in rows:
        kind = 'search' if r['kind'] != 'uncited' else 'verify'
        g = groups.setdefault((r['person'], kind), {'person': r['person'], 'label': r['label'], 'task': kind,
                                                   'priority': r['priority'], 'gaps': []})
        g['priority'] = max(g['priority'], r['priority'])
        g['gaps'].append('%s: %s' % (r['kind'], r['what']))
    out = list(groups.values())
    out.sort(key=lambda g: (-g['priority'], g['person'], g['task']))
    return out


def as_queue_line(r, source=''):
    if 'gaps' in r:
        kind, query, note = r['task'], r['label'], '; '.join(r['gaps'])
    else:
        kind = 'search' if r['kind'] != 'uncited' else 'verify'
        query = '%s %s' % (r['label'], r['what'])
        note = '%s; %s' % (r['kind'], r['what'])
    parts = ['gwt-queue add --person', shlex.quote(r['person']), '--label', shlex.quote(r['label']),
             '--kind', kind, '--query', shlex.quote(query), '--priority', str(r['priority']),
             '--note', shlex.quote(note)]
    if source:
        parts += ['--source', shlex.quote(source)]
    return ' '.join(parts + ['--dedupe'])


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-leads', description=__doc__.split('\n')[0])
    p.add_argument('dump')
    p.add_argument('--root', help='Gramps id of the person whose ancestors rank first')
    p.add_argument('--kinds', default=','.join(KINDS))
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--format', choices=('queue', 'tsv', 'json'), default='tsv')
    p.add_argument('--source', default='', help='source name to put on every queue task (queue format)')
    p.add_argument('--per-gap', action='store_true', help='queue format: one task per gap instead of one per person')
    args = p.parse_args(argv)
    kinds = tuple(k for k in args.kinds.split(',') if k)
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        p.error('unknown kind: %s' % ', '.join(bad))
    try:
        rows = leads(load(args.dump), args.root, kinds)
    except BadInput as exc:
        print('gwt-leads: %s' % exc, file=sys.stderr)
        return 2
    if args.format == 'queue' and not args.per_gap:
        rows = group_by_person(rows)
    if args.limit:
        rows = rows[:args.limit]
    for r in rows:
        if args.format == 'queue':
            print(as_queue_line(r, args.source))
        elif args.format == 'json':
            print(json.dumps(r, ensure_ascii=False))
        else:
            print('\t'.join(str(r[c]) for c in ('priority', 'person', 'label', 'kind', 'what')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
