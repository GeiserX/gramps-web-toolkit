"""Offline evidence shared across recorded family components, without inferring kinship."""
from collections import defaultdict

from .tree import BadInput


def component_evidence(tree, root):
    """Group through recorded families; shared case evidence adds no family edge.

    Include directly adjacent families from either reference direction and their
    events. Do not follow relatives, citations to sources, or shared places.
    """
    if tree.partial:
        raise BadInput('component-evidence needs a full JSON dump, not a .gramps backup')
    people, families = tree.objs['people'], tree.objs['families']
    roots = [h for h, p in people.items() if p.get('gramps_id') == root]
    if len(roots) != 1:
        raise BadInput('root must identify exactly one person by Gramps ID')
    adjacent = defaultdict(set)
    members = defaultdict(set)
    for fh, family in families.items():
        refs = [family.get('father_handle'), family.get('mother_handle')]
        refs += [r.get('ref') for r in family.get('child_ref_list', [])]
        for ph in refs:
            if ph in people:
                adjacent[ph].add(fh)
                members[fh].add(ph)
    for ph, person in people.items():
        for fh in person.get('family_list', []) + person.get('parent_family_list', []):
            if fh in families:
                adjacent[ph].add(fh)
                members[fh].add(ph)
    component = {}
    for ph in sorted(people):
        if ph in component:
            continue
        todo = [ph]
        seen_families = set()
        while todo:
            current = todo.pop()
            if current in component:
                continue
            component[current] = ph
            for fh in adjacent[current] - seen_families:
                seen_families.add(fh)
                todo.extend(members[fh])
    owners = defaultdict(set)
    for ph, person in people.items():
        pending = [('people', ph)] + [('families', fh) for fh in sorted(adjacent[ph])]
        seen = set()
        while pending:
            typ, handle = pending.pop()
            if (typ, handle) in seen:
                continue
            seen.add((typ, handle))
            obj = tree.objs[typ].get(handle, {})
            if typ in ('notes', 'media'):
                continue
            for key, dest in (('note_list', 'notes'), ('citation_list', 'citations')):
                for target in obj.get(key, []):
                    if target in tree.objs[dest]:
                        owners[(dest, target)].add(ph)
                        if dest == 'citations':
                            pending.append((dest, target))
            for ref in obj.get('media_list', []):
                target = ref.get('ref')
                if target in tree.objs['media']:
                    owners[('media', target)].add(ph)
            if typ in ('people', 'families'):
                pending.extend(('events', ref.get('ref')) for ref in obj.get('event_ref_list', []))
    shared = []
    for (typ, handle), persons in sorted(owners.items()):
        if len({component[p] for p in persons}) > 1:
            shared.append({'type': typ, 'id': tree.gid(typ, handle),
                           'people': sorted(tree.gid('people', p) for p in persons)})
    grouped = defaultdict(list)
    for ph, key in component.items():
        grouped[key].append(tree.gid('people', ph))
    return {'root': root, 'components': sorted([
                {'people': sorted(ids), 'root_connected': key == component[roots[0]]}
                for key, ids in grouped.items()], key=lambda row: row['people']),
            'shared_evidence': shared,
            'limits': ['Structural components are not proven relationships.',
                       'Shared citations, notes or media are review leads, not identity or kinship proof.',
                       'Includes private data; keep the report private. No deletion or merge recommendation.']}
