import io, json, os, sys, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_report as R

HERE = os.path.dirname(os.path.abspath(__file__))
LINKS = os.path.join(HERE, 'fixtures', 'links-tree.json')


def load_links():
    T = R.load(LINKS)
    return T, R.links(T, 'p_root')


def outside_members(rep):
    return {m for comp in rep['outside'].values() for m in comp['members']}


class LinksTest(unittest.TestCase):

    def test_counts_cover_every_person(self):
        T, rep = load_links()
        c = rep['counts']
        self.assertEqual(c['people'], 17)
        self.assertEqual(c['connected'] + c['union_only'] + c['outside'], 17)
        self.assertEqual(c['connected'], 7)
        self.assertEqual(c['union_only'], 5)
        self.assertEqual(c['outside'], 5)
        self.assertEqual(c['components_outside'], 3)

    def test_collateral_affinity_and_spouse_parents_connected(self):
        T, rep = load_links()
        for h in ('p_sibling', 'p_spouse', 'p_spfather', 'p_spmother'):
            self.assertEqual(rep['people'][h]['status'], 'connected', h)
        # in-laws connect through the marriage edge, labeled as such
        path = rep['people']['p_spfather']['path']
        self.assertEqual(path[0]['via'], 'spouse')
        self.assertEqual(path[0]['person'], 'p_spouse')

    def test_surname_witness_and_association_are_not_edges(self):
        T, rep = load_links()
        self.assertIn('p_homonym', outside_members(rep))

    def test_one_sided_parent_pointer_protects_without_repair(self):
        T, rep = load_links()
        self.assertEqual(rep['people']['p_unilat_father']['status'], 'union_only')
        self.assertEqual(rep['people']['p_unilat_mother']['status'], 'union_only')
        self.assertIn(('person_parent_family_without_family_child', 'p_sibling', 'f_d'), rep['problems'])
        self.assertEqual(T['families']['f_d']['child_ref_list'], [])

    def test_one_sided_spouse_pointer_protects_family_members(self):
        T, rep = load_links()
        for h in ('p_spouse2', 'p_child2', 'p_spref'):
            self.assertEqual(rep['people'][h]['status'], 'union_only', h)

    def test_missing_family_is_a_problem_not_an_edge(self):
        T, rep = load_links()
        self.assertIn(('missing_parent_family', 'p_orphan', 'f_missing'), rep['problems'])
        self.assertIn('p_orphan', outside_members(rep))

    def test_cycle_terminates_and_stays_outside(self):
        T, rep = load_links()
        for h in ('p_cyc_a', 'p_cyc_b', 'p_cyc_c'):
            self.assertIn(h, outside_members(rep))
        ring = next(c for c in rep['outside'].values() if c['size'] == 3)
        self.assertEqual(sorted(ring['members']), ['p_cyc_a', 'p_cyc_b', 'p_cyc_c'])

    def test_weak_tag_marks_the_path_weakest_link(self):
        T, rep = load_links()
        self.assertEqual(rep['people']['p_spfather']['weakest'], 'weak')

    def test_never_authorizes_deletion(self):
        T, rep = load_links()
        self.assertFalse(rep['deletion_authorized'])

    def test_cli_text_and_json(self):
        for argv in (['links', '--root', 'L0001', LINKS],
                     ['links', '--root', 'L0001', '--lang', 'es', LINKS],
                     ['links', '--root', 'L0001', '--json', LINKS]):
            out = io.StringIO()
            with redirect_stdout(out):
                code = R.main(argv)
            self.assertEqual(code, 0, argv)
            text = out.getvalue()
            if '--json' in argv:
                rep = json.loads(text)
                self.assertEqual(rep['counts']['people'], 17)
                self.assertFalse(rep['deletion_authorized'])
            else:
                self.assertIn('Homer Example', text)
        out = io.StringIO()
        with redirect_stdout(out):
            code = R.main(['links', '--root', 'L9999', LINKS])
        self.assertEqual(code, 2)


def mini_tree(people, families, tags=(), citations=()):
    T = {k: {} for k in R.CLASSES.values()}
    T['people'] = {h: dict(handle=h, gramps_id=h, **v) for h, v in people.items()}
    T['families'] = {h: dict(handle=h, **v) for h, v in families.items()}
    T['tags'] = {h: dict(handle=h, name=n) for h, n in tags}
    T['citations'] = {h: dict(handle=h, **v) for h, v in citations}
    return T


class LinksReviewReprosTest(unittest.TestCase):
    """Independent-review repros: every one must stay fixed."""

    def test_person_family_pointer_to_family_without_them_is_a_problem(self):
        T = mini_tree({'root': {'family_list': ['f']}, 'spouse': {'family_list': ['f']},
                       'third': {'family_list': ['f']}},
                      {'f': {'father_handle': 'root', 'mother_handle': 'spouse'}})
        rep = R.links(T, 'root')
        self.assertIn(('person_family_without_family_spouse', 'third', 'f'), rep['problems'])
        self.assertNotIn(('person_family_without_family_spouse', 'root', 'f'), rep['problems'])
        self.assertEqual(rep['people']['third']['status'], 'union_only')

    def test_step_origins_expose_the_recorded_relation(self):
        T = mini_tree({'root': {'family_list': ['f']}, 'child': {'parent_family_list': ['f']}},
                      {'f': {'father_handle': 'root',
                             'child_ref_list': [{'ref': 'child', 'frel': 'Foster', 'mrel': 'Birth'}]}})
        step = R.links(T, 'root')['people']['child']['path'][0]
        self.assertIn({'type': 'child', 'family': 'f', 'frel': 'Foster', 'mrel': 'Birth'},
                      step['origins'])

    def test_only_a_recorded_birth_relation_is_labeled_blood(self):
        for rel, expected in (('Birth', 'blood'), ('', 'family'), ('Unknown', 'family'),
                              ('Adopted', 'family'), ('Foster', 'family-nonblood'),
                              ('Stepchild', 'family-nonblood'), ('Sponsored', 'family-nonblood')):
            T = mini_tree({'root': {'family_list': ['f']}, 'child': {'parent_family_list': ['f']}},
                          {'f': {'father_handle': 'root',
                                 'child_ref_list': [{'ref': 'child', 'frel': rel}]}})
            step = R.links(T, 'root')['people']['child']['path'][0]
            self.assertEqual(step['via'], expected, rel)

    def test_non_blood_child_relation_is_not_labeled_blood(self):
        T = mini_tree({'root': {'family_list': ['f']}, 'child': {'parent_family_list': ['f']}},
                      {'f': {'father_handle': 'root',
                             'child_ref_list': [{'ref': 'child', 'frel': 'Foster'}]}})
        step = R.links(T, 'root')['people']['child']['path'][0]
        self.assertEqual(step['via'], 'family-nonblood')
        self.assertEqual(rep_status(T, 'root', 'child'), 'connected')

    def test_weak_spouse_family_tag_follows_the_edge_origin(self):
        T = mini_tree({'root': {'family_list': ['f']}, 'spouse': {}},
                      {'f': {'father_handle': 'root', 'mother_handle': 'spouse', 'tag_list': ['tw']}},
                      tags=[('tw', 'hypothesis')])
        rep = R.links(T, 'root')
        self.assertEqual(rep['people']['spouse']['weakest'], 'weak')

    def test_family_spouse_without_person_family_is_a_problem(self):
        T = mini_tree({'root': {'family_list': ['f']}, 'spouse': {}},
                      {'f': {'father_handle': 'root', 'mother_handle': 'spouse'}})
        rep = R.links(T, 'root')
        self.assertIn(('family_spouse_without_person_family', 'spouse', 'f'), rep['problems'])
        self.assertNotIn(('family_spouse_without_person_family', 'root', 'f'), rep['problems'])

    def test_parent_pointer_connects_family_children_without_parents(self):
        T = mini_tree({'root': {'parent_family_list': ['f']},
                       'child': {'parent_family_list': ['f']},
                       'father': {'family_list': ['f']}},
                      {'f': {'child_ref_list': [{'ref': 'child'}]}})
        rep = R.links(T, 'root')
        self.assertEqual(rep['counts']['outside'], 0)
        self.assertEqual(rep['people']['child']['status'], 'union_only')
        self.assertEqual(rep['people']['father']['status'], 'union_only')

    def test_parallel_families_weakest_strength_is_order_independent(self):
        def build(order):
            fams = {'f1': {'father_handle': 'root',
                           'child_ref_list': [{'ref': 'child', 'citation_list': ['c']}]},
                    'f2': {'father_handle': 'root', 'tag_list': ['tw'],
                           'child_ref_list': [{'ref': 'child'}]}}
            fams = {k: fams[k] for k in order}
            return mini_tree({'root': {'family_list': ['f1', 'f2']},
                              'child': {'parent_family_list': ['f1', 'f2']}},
                             fams, tags=[('tw', 'hypothesis')],
                             citations=[('c', {'confidence': 4})])
        a = R.links(build(('f1', 'f2')), 'root')['people']['child']['weakest']
        b = R.links(build(('f2', 'f1')), 'root')['people']['child']['weakest']
        self.assertEqual(a, b)
        self.assertEqual(a, 'weak')


def rep_status(T, root, h):
    return R.links(T, root)['people'][h]['status']


if __name__ == '__main__':
    unittest.main()
