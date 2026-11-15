#!/usr/bin/env python3
"""Build report/fixtures/links-tree.json — invented people only (repo rule)."""
import json, os

def person(h, gid, first, surname, gender=None, parents=(), families=(), tags=()):
    return {'handle': h, 'gramps_id': gid, 'gender': gender,
            'primary_name': {'first_name': first, 'surname_list': [{'surname': surname}]},
            'parent_family_list': list(parents), 'family_list': list(families),
            'tag_list': list(tags)}

def family(h, a, b, chs):
    return {'handle': h, 'father_handle': a, 'mother_handle': b,
            'child_ref_list': [{'ref': c} for c in chs]}

people = [
    person('p_root', 'L0001', 'Root', 'Example', 1, parents=['f_a'], families=['f_b']),
    person('p_father', 'L0002', 'Father', 'Example', 1, families=['f_a']),
    person('p_mother', 'L0003', 'Mother', 'Sample', 0, families=['f_a']),
    person('p_sibling', 'L0004', 'Sibling', 'Example', 0, parents=['f_a', 'f_d'], families=['f_e']),  # one-sided parent AND spouse pointers
    person('p_spouse', 'L0005', 'Spouse', 'Inlaw', 0, parents=['f_c'], families=['f_b'], tags=['t_weak']),
    person('p_spfather', 'L0006', 'Spfather', 'Inlaw', 1, families=['f_c']),
    person('p_spmother', 'L0007', 'Spmother', 'Inlaw', 0, families=['f_c']),
    person('p_homonym', 'L0008', 'Homer', 'Example', 1),  # same surname + association, no family edge
    person('p_unilat_father', 'L0010', 'Unifather', 'Pointer', 1, families=['f_d']),
    person('p_unilat_mother', 'L0011', 'Unimother', 'Pointer', 0, families=['f_d']),
    person('p_spref', 'L0012', 'Spouseref', 'Guard', 1, families=['f_e']),
    person('p_spouse2', 'L0013', 'Otherspouse', 'Guard', 0, families=['f_e']),
    person('p_child2', 'L0014', 'Childtwo', 'Guard', 1, parents=['f_e']),
    person('p_cyc_a', 'L0015', 'Cyca', 'Ring', 1, parents=['f_g3'], families=['f_g1']),
    person('p_cyc_b', 'L0016', 'Cycb', 'Ring', 1, parents=['f_g1'], families=['f_g2']),
    person('p_cyc_c', 'L0017', 'Cycc', 'Ring', 1, parents=['f_g2'], families=['f_g3']),
    person('p_orphan', 'L0018', 'Orphan', 'Missing', 1, parents=['f_missing']),
]
families = [
    family('f_a', 'p_father', 'p_mother', ['p_root', 'p_sibling']),
    family('f_b', 'p_root', 'p_spouse', []),
    family('f_c', 'p_spfather', 'p_spmother', ['p_spouse']),
    family('f_d', 'p_unilat_father', 'p_unilat_mother', []),   # no child_ref back to p_sibling
    family('f_e', 'p_spouse2', None, ['p_child2']),           # p_spref points here via family_list
    family('f_g1', 'p_cyc_a', None, ['p_cyc_b']),
    family('f_g2', 'p_cyc_b', None, ['p_cyc_c']),
    family('f_g3', 'p_cyc_c', None, ['p_cyc_a']),
]
tags = [{'handle': 't_weak', 'name': 'hipotesis'}]
people[7]['person_ref_list'] = [{'ref': 'p_root', 'description': 'witness'}]
people[7]['event_ref_list'] = [{'ref': 'ev_1', 'role': 'Witness'}]
events = [{'handle': 'ev_1', 'type': 'Baptism', 'date': {'year': 1900}}]
people[0]['event_ref_list'] = [{'ref': 'ev_1', 'role': 'Primary'}]
data = {'events': events, 'people': people, 'families': families, 'citations': [], 'media': [],
        'notes': [], 'tags': tags, 'places': [], 'sources': []}
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'links-tree.json')
with open(out, 'w', encoding='utf-8') as fh:
    json.dump(data, fh, ensure_ascii=False, indent=1)
print('wrote', out)
