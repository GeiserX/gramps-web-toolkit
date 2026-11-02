import copy, io, json, os, re, subprocess, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_report as R

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
CLI = os.path.join(HERE, 'gwt-report')
KIN = os.path.join(FIX, 'kin-tree.json')
OLD, NEW = os.path.join(FIX, 'digest-old.json'), os.path.join(FIX, 'digest-new.json')

# (A, B, English, Spanish, French): what B is to A. Line A is sons, line B daughters (see fixtures/make_fixtures.py)
NAMES = [
    ('A1', 'B1', 'sister', 'hermana', 'sœur'),
    ('A2', 'B2', 'first cousin', 'prima hermana', 'cousine germaine'),
    ('A3', 'B3', 'second cousin', 'prima segunda', 'cousine issue de germain'),
    ('A4', 'B4', 'third cousin', 'prima tercera', "cousine issue d'issue de germain"),
    ('A2', 'B3', 'first cousin once removed', 'sobrina segunda', 'cousine germaine, une génération au-dessous'),
    ('B3', 'A2', 'first cousin once removed', 'tío segundo', 'cousin germain, une génération au-dessus'),
    ('A2', 'B4', 'first cousin twice removed', 'sobrina nieta segunda', 'cousine germaine, deux générations au-dessous'),
    ('B4', 'A2', 'first cousin twice removed', 'tío abuelo segundo', 'cousin germain, deux générations au-dessus'),
    ('A3', 'B4', 'second cousin once removed', 'sobrina tercera', 'cousine issue de germain, une génération au-dessous'),
    ('B5', 'A3', 'second cousin twice removed', 'tío abuelo tercero', 'cousin issu de germain, deux générations au-dessus'),
    ('A4', 'B5', 'third cousin once removed', 'sobrina cuarta',
     "cousine issue d'issue de germain, une génération au-dessous"),
    ('A4', 'B6', 'third cousin twice removed', 'sobrina nieta cuarta',
     "cousine issue d'issue de germain, deux générations au-dessous"),
    ('A1', 'B2', 'niece', 'sobrina', 'nièce'),
    ('B2', 'A1', 'uncle', 'tío', 'oncle'),
    ('A1', 'B3', 'great-niece', 'sobrina nieta', 'petite-nièce'),
    ('B3', 'A1', 'great-uncle', 'tío abuelo', 'grand-oncle'),
    ('B4', 'A1', 'great-great-uncle', 'tío bisabuelo', 'arrière-grand-oncle'),
    ('A1', 'H1', 'half-sister', 'media hermana', 'demi-sœur'),
    ('A2', 'H2', 'half first cousin', 'medio primo hermano', 'demi-cousin germain'),
    ('A3', 'H1', 'half great-aunt', 'media tía abuela', 'demi-grand-tante'),
    ('A2', 'I1', 'grandfather', 'abuelo', 'grand-père'),
    ('B3', 'I1', 'great-grandfather', 'bisabuelo', 'arrière-grand-père'),
    ('B4', 'I2', 'great-great-grandmother', 'tatarabuela', 'arrière-arrière-grand-mère'),
    ('B6', 'I1', '4x great-grandfather', 'antepasado en 6 generaciones', 'ancêtre à la 6e génération'),
    ('I1', 'B6', '4x great-granddaughter', 'descendiente en 6 generaciones', 'descendante à la 6e génération'),
    ('I1', 'A2', 'grandson', 'nieto', 'petit-fils'),
    ('A2', 'A2', 'same person', 'la misma persona', 'la même personne'),
]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = R.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def unlink(T, child, family):
    """Take a child out of a family in memory, both sides."""
    f = T['people'][child]
    f['parent_family_list'] = [h for h in f['parent_family_list'] if h != family]
    fam = T['families'][family]
    fam['child_ref_list'] = [c for c in fam['child_ref_list'] if c['ref'] != child]


def big_pedigree(path, gens=11, drop=()):
    """An invented full pedigree with no implex: P1 and every ancestor up to `gens` generations (Ahnentafel ids
    P2, P3, ...), plus Cf, a son of a brother of P1's father, and Cm, a son of a sister of P1's mother. Ids in
    `drop` are left out. Written as a gwt-apply snapshot."""
    people, families = {}, []

    def add(gid, gender):
        people[gid] = {'handle': 'h' + gid, 'gramps_id': gid, 'gender': gender,
                       'primary_name': {'first_name': gid, 'surname_list': []}, 'parent_family_list': []}

    def fam(gid, father, mother, children):
        families.append({'handle': 'h' + gid, 'gramps_id': gid, 'father_handle': 'h' + father,
                         'mother_handle': 'h' + mother,
                         'child_ref_list': [{'ref': 'h' + c, 'frel': 'Birth', 'mrel': 'Birth'} for c in children]})
        for c in children:
            people[c]['parent_family_list'].append('h' + gid)
    for n in range(1, 2 ** (gens + 1)):
        add('P%d' % n, n % 2 if n > 1 else 1)
    for gid, g in (('UF', 1), ('Cf', 1), ('WF', 0), ('UM', 0), ('Cm', 1), ('HM', 1)):
        add(gid, g)
    for n in range(1, 2 ** gens):
        kids = ['P%d' % n] + (['UF'] if n == 2 else ['UM'] if n == 3 else [])
        fam('F%d' % n, 'P%d' % (2 * n), 'P%d' % (2 * n + 1), kids)
    fam('FUF', 'UF', 'WF', ['Cf'])
    fam('FUM', 'HM', 'UM', ['Cm'])
    people = [p for gid, p in people.items() if gid not in drop]
    keep = {p['handle'] for p in people}
    for f in families:
        f['child_ref_list'] = [c for c in f['child_ref_list'] if c['ref'] in keep]
    snap = {'format': 'gwt-apply-snapshot/1', 'tree': 'example', 'taken': '2026-01-01T00:00:00+00:00',
            'objects': {'Person': people, 'Family': families}}
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(snap, fh)
    return path


class KinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.T = R.load(KIN)

    def kin(self, a, b, T=None):
        T = T or self.T
        return R.kin(T, R.person_handle(T, a), R.person_handle(T, b))

    def test_names_in_three_languages(self):
        for a, b, en, es, fr in NAMES:
            rels = self.kin(a, b)['relationships']
            self.assertTrue(rels, (a, b))
            self.assertEqual(rels[0]['name'], {'en': en, 'es': es, 'fr': fr}, (a, b))

    def test_second_cousins_share_a_couple_with_ahnentafel_numbers(self):
        r = self.kin('A3', 'B3')['relationships']
        self.assertEqual(len(r), 1)
        self.assertEqual([(c, na, nb) for c, na, nb in r[0]['ancestors']], [('hI1', 8, 14), ('hI2', 9, 15)])
        self.assertEqual(r[0]['generations'], [3, 3])
        self.assertFalse(r[0]['half'])
        # a direct line is one relationship, never one more per ancestor above the nearer person
        self.assertEqual(len(self.kin('A4', 'A2')['relationships']), 1)
        self.assertEqual(len(self.kin('B3', 'I1')['relationships']), 1)

    def test_link_marks_are_counted_per_path(self):
        r = self.kin('A3', 'B3')['relationships'][0]
        # A3 tagged Hipótesis (weak), A2's link [probable], B2's link cited at confidence 3, three plain
        self.assertEqual(r['links'], {'confirmed': 1, 'probable': 1, 'weak': 1, 'unmarked': 3})

    def test_removing_one_parent_link_leaves_no_blood_path(self):
        self.assertEqual(self.kin('A3', 'B3')['relationships'][0]['name']['en'], 'second cousin')
        T = copy.deepcopy(self.T)
        unlink(T, 'hA2', 'hFA1')
        self.assertEqual(self.kin('A3', 'B3', T)['relationships'], [])
        code, out, _ = run('kin', KIN, 'A3', 'B3')
        self.assertIn('second cousin', out)

    def test_in_laws_and_stepchildren_are_not_blood(self):
        for a, b in (('A2', 'HB3'), ('B1', 'HB2'), ('A1', 'S1'), ('A1', 'WA2'), ('A1', 'WA3')):
            self.assertEqual(self.kin(a, b)['relationships'], [], (a, b))
        code, out, _ = run('kin', KIN, 'A2', 'HB3')
        self.assertEqual(code, 0)
        self.assertIn('no blood path', out)

    def test_adoption_is_followed_and_flagged(self):
        r = self.kin('A1', 'K1')['relationships'][0]
        self.assertEqual(r['name']['en'], 'son')
        self.assertTrue(r['adopted'])
        self.assertFalse(self.kin('A1', 'A2')['relationships'][0]['adopted'])
        self.assertIn('through an adoption', run('kin', KIN, 'A1', 'K1')[1])

    def test_one_recorded_parent_is_not_half(self):
        T = copy.deepcopy(self.T)
        T['people']['hK1']['parent_family_list'].append('hFC')  # a second child of a family with a father only
        T['families']['hFC']['child_ref_list'].append({'ref': 'hK1', 'frel': 'Birth', 'mrel': 'Birth'})
        r = self.kin('C2', 'K1', T)['relationships']
        self.assertEqual(r[0]['name']['en'], 'brother')
        self.assertFalse(r[0]['half'])
        # Adam's child in a family with no mother recorded, against A1 of Adam and Eve: two families, so half
        T['people']['hN9'] = dict(T['people']['hK1'], handle='hN9', gramps_id='N9', parent_family_list=['hFN'])
        T['families']['hFN'] = {'handle': 'hFN', 'gramps_id': 'FN', 'father_handle': 'hI1', 'mother_handle': None,
                                'child_ref_list': [{'ref': 'hN9', 'frel': 'Birth', 'mrel': 'Birth'}]}
        self.assertEqual(self.kin('hN9', 'A1', T)['relationships'][0]['name']['en'], 'half-brother')

    def test_implex_couple_with_ahnentafel_numbers(self):
        rep = self.kin('X1', 'A1')
        self.assertEqual(rep['implex']['a'], [{'people': ['hI1', 'hI2'], 'numbers': [[8, 9], [14, 15]]}])
        self.assertEqual(rep['implex']['b'], [])
        # X1's parents are first cousins, so A1 is X1's great-uncle once, through C1, and also through B1
        self.assertEqual(self.kin('A3', 'B3')['implex'], {'a': [], 'b': []})
        out = run('kin', KIN, 'X1', 'A1')[1]
        self.assertIn('8-9, 14-15', out)
        T = copy.deepcopy(self.T)  # Adam's parents are then reached twice too, but only the lowest couple is listed
        T['people']['hI3']['parent_family_list'] = []
        T['people']['hI1']['parent_family_list'].append('hF2')
        T['families']['hF2']['child_ref_list'].append({'ref': 'hI1', 'frel': 'Birth', 'mrel': 'Birth'})
        T['families']['hF2']['father_handle'] = 'hHV'
        self.assertEqual(self.kin('X1', 'A1', T)['implex']['a'],
                         [{'people': ['hI1', 'hI2'], 'numbers': [[8, 9], [14, 15]]}])

    def test_implex_gives_one_relationship_per_line_of_descent(self):
        rels = self.kin('A1', 'X1')['relationships']
        self.assertEqual(sorted(r['name']['en'] for r in rels), ['great-niece', 'great-niece'])
        self.assertEqual(sorted(tuple(r['path_b'][:2]) for r in rels), [('hX1', 'hC2'), ('hX1', 'hE2')])

    def test_double_cousins_have_both_relationships(self):
        # DA's line to Gil and Gwen runs through Pete, a nearer common ancestor; DB's does not, so it counts
        rels = self.kin('DA', 'DB')['relationships']
        self.assertEqual([r['name'] for r in rels], [
            {'en': 'first cousin', 'es': 'prima hermana', 'fr': 'cousine germaine'},
            {'en': 'second cousin', 'es': 'prima segunda', 'fr': 'cousine issue de germain'}])
        self.assertEqual([(c, na, nb) for c, na, nb in rels[1]['ancestors']], [('hG1', 8, 14), ('hG2', 9, 15)])
        self.assertFalse(any(r['half'] for r in rels))

    def test_own_ancestor_loop_ends(self):
        T = copy.deepcopy(self.T)
        T['people']['hI3']['parent_family_list'].append('hF3')  # Lilith recorded as her own grandchild: bad data
        T['families']['hF3']['child_ref_list'].append({'ref': 'hI3', 'frel': 'Birth', 'mrel': 'Birth'})
        rels = self.kin('A2', 'H2', T)['relationships']
        self.assertEqual([r['name']['en'] for r in rels], ['half first cousin'])

    def test_weak_tag_option(self):
        code, out, _ = run('kin', KIN, 'A3', 'B3', '--json', '--weak-tag', 'nothing-like-it')
        self.assertEqual(json.loads(out)['relationships'][0]['links']['weak'], 0)

    def test_spanish_and_french_weak_words_are_defaults(self):
        for name in ('r1-débil', 'Lien faible'):
            T = copy.deepcopy(self.T)
            T['tags']['htag1']['name'] = name
            self.assertEqual(self.kin('A3', 'B3', T)['relationships'][0]['links']['weak'], 1, name)

    def test_paths_stop_at_30_generations(self):
        T = {'people': {}, 'families': {}}
        for n in range(32):  # L0 is the child of L1, ... L31
            T['people']['hL%d' % n] = {'handle': 'hL%d' % n, 'gramps_id': 'L%d' % n,
                                       'parent_family_list': ['hFL%d' % n] if n < 31 else []}
            if n < 31:
                T['families']['hFL%d' % n] = {'handle': 'hFL%d' % n, 'father_handle': 'hL%d' % (n + 1),
                                              'child_ref_list': [{'ref': 'hL%d' % n}]}
        paths, _ = R.ancestor_paths(T, 'hL0')
        self.assertIn('hL30', paths)
        self.assertNotIn('hL31', paths)

    def test_json_and_lang(self):
        code, out, _ = run('kin', KIN, 'A3', 'B4', '--json')
        data = json.loads(out)
        self.assertEqual(data['relationships'][0]['name']['es'], 'sobrina tercera')
        self.assertEqual(data['relationships'][0]['ancestors'][0]['id'], 'I1')
        self.assertEqual(data['relationships'][0]['path_b'], ['B4', 'B3', 'B2', 'B1', 'I1'])
        self.assertIn('cousine issue de germain', run('kin', KIN, 'A3', 'B4', '--lang', 'fr')[1])

    def test_unknown_person_and_bad_file_exit_2(self):
        self.assertEqual(run('kin', KIN, 'A3', 'Z9')[0], 2)
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            fh.write('{"objects": {"people": []}}')
        try:
            code, _, err = run('kin', fh.name, 'A1', 'B1')
        finally:
            os.unlink(fh.name)
        self.assertEqual(code, 2)
        self.assertIn('no families list', err)

    def test_a_big_pedigree_keeps_both_sides(self):
        # 11 full generations hold more than 2,000 ancestor paths: the cut drops the most distant, never one side
        with tempfile.TemporaryDirectory() as td:
            T = R.load(big_pedigree(os.path.join(td, 'ped.json')))
        for cousin in ('Cf', 'Cm'):
            rep = self.kin('P1', cousin, T)
            self.assertEqual([r['name']['en'] for r in rep['relationships']], ['first cousin'], cousin)
            self.assertTrue(rep['truncated'])

    def test_wrapper_runs(self):
        out = subprocess.run([sys.executable, CLI, 'kin', KIN, 'A3', 'B3', '--lang', 'es'], capture_output=True,
                             text=True, check=True).stdout
        self.assertIn('prima segunda', out)


class DigestTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.told = os.path.join(self.td.name, 'told.tsv')

    def tearDown(self):
        self.td.cleanup()

    def digest(self, *extra):
        code, out, err = run('digest', '--from', OLD, '--to', NEW, *extra)
        self.assertEqual(code, 0, err)
        return out

    def test_new_objects_grouped_by_side_and_distance(self):
        out = self.digest('--root', 'I1', '--base-url', 'https://gramps.example.org/')
        sides = [l for l in out.splitlines() if l.startswith('## ')]
        self.assertEqual(sides, ["## father's side", "## mother's side", '## not blood kin'])
        people = [l.split(' (')[0][4:] for l in out.splitlines() if l.startswith('### ')]
        self.assertEqual(people, ['Paul Gale', 'Lena Gale', 'Rosa Pine', 'Tim Reed'])  # grandfather (2) before cousin (4)
        self.assertIn('Paul Gale (I5), grandfather https://gramps.example.org/person/I5', out)
        self.assertIn('new event: Baptism, 1890, Greenford https://gramps.example.org/event/E1 '
                      'https://archive.example.org/register/12.jpg', out)
        self.assertIn('new media: Register page 12 https://gramps.example.org/media/O1', out)
        self.assertIn('new person: Rosa Pine [weak]', out)
        self.assertIn('1 weak', out)

    def test_old_objects_and_private_people_stay_out(self):
        out = self.digest('--root', 'I1')
        for gone in ('Otto', 'Ugo', 'Mia', 'Ivo'):
            self.assertNotIn(gone, out)
        self.assertIn('Ivo', self.digest('--include-private'))

    def test_private_event_and_private_person_stay_out(self):
        old, new = R.load(OLD), R.load(NEW)
        new['events']['hE2'] = dict(new['events']['hE1'], handle='hE2', gramps_id='E2', private=True)
        new['people']['hI5']['event_ref_list'].append({'ref': 'hE2', 'role': 'Primary'})
        new['events']['hE3'] = dict(new['events']['hE1'], handle='hE3', gramps_id='E3', citation_list=[])
        new['people']['hI9']['event_ref_list'].append({'ref': 'hE3', 'role': 'Primary'})

        def seen(include):
            groups = R.digest(old, new, include_private=include)
            return {(g['person'], it['id']) for g in groups for it in g['items']}
        self.assertFalse({('hI5', 'E2'), ('hI9', 'E3')} & seen(False))
        self.assertLessEqual({('hI5', 'E2'), ('hI9', 'E3')}, seen(True))

    def test_told_file_suppresses_what_that_audience_heard(self):
        with open(self.told, 'w', encoding='utf-8') as fh:
            fh.write('audience\tobject\tdate\nfamily\tI12\t2026-01-05\nfamily\tE1\t2026-01-05\nfriends\tI8\t2026-01-05\n')
        out = self.digest('--root', 'I1', '--told', self.told, '--audience', 'family')
        self.assertNotIn('Lena', out)
        self.assertNotIn('new event', out)
        self.assertIn('Rosa Pine', out)           # told to another audience only
        self.assertIn('new citation', out)

    def test_mark_appends_and_a_second_run_is_empty(self):
        self.digest('--root', 'I1', '--told', self.told, '--audience', 'family', '--mark')
        with open(self.told, encoding='utf-8') as fh:
            rows = [l.split('\t')[:2] for l in fh.read().splitlines()]
        self.assertEqual(rows[0], ['audience', 'object'])
        self.assertEqual(sorted(r[1] for r in rows[1:]), ['C1', 'E1', 'I11', 'I12', 'I8', 'O1'])
        out = self.digest('--root', 'I1', '--told', self.told, '--audience', 'family')
        self.assertIn('Nothing new.', out)
        self.assertIn('Lena', self.digest('--root', 'I1', '--told', self.told, '--audience', 'others'))

    def test_mark_starts_a_new_line_when_the_file_lacks_one(self):
        with open(self.told, 'w', encoding='utf-8') as fh:
            fh.write('audience\tobject\tdate\nfamily\tE1\t2026-02-03')  # edited by hand, no last newline
        self.digest('--root', 'I1', '--told', self.told, '--audience', 'family', '--mark')
        self.assertEqual(R.read_told(self.told, 'family'), {'C1', 'E1', 'I11', 'I12', 'I8', 'O1'})

    def test_unreadable_or_unwritable_told_file_exits_2(self):
        with open(self.told, 'wb') as fh:
            fh.write('family\tI12\tmarz\xe9\n'.encode('latin-1'))
        folder = os.path.join(self.td.name, 'folder')
        os.mkdir(folder)
        for told, extra in ((self.told, ()), (folder, ()), (os.path.join(self.td.name, 'none', 'x.tsv'), ('--mark',))):
            code, _, err = run('digest', '--from', OLD, '--to', NEW, '--told', told, '--audience', 'family', *extra)
            self.assertEqual(code, 2, told)
            self.assertIn('told file', err)

    def test_two_different_trees_exit_2_and_swapped_snapshots_warn(self):
        other = os.path.join(self.td.name, 'other.json')
        with open(NEW, encoding='utf-8') as fh:
            data = json.load(fh)
        data['tree'] = 'another'
        with open(other, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)
        code, out, err = run('digest', '--from', OLD, '--to', other)
        self.assertEqual((code, out), (2, ''))
        self.assertIn('two different trees', err)
        code, out, err = run('digest', '--from', NEW, '--to', OLD)
        self.assertEqual(code, 0)
        self.assertIn('are they swapped?', err)
        self.assertNotIn('swapped', run('digest', '--from', OLD, '--to', NEW)[2])

    def test_told_needs_audience(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                R.main(['digest', '--from', OLD, '--to', NEW, '--told', self.told])
        self.assertEqual(cm.exception.code, 2)

    def test_bad_told_row_exits_2(self):
        with open(self.told, 'w', encoding='utf-8') as fh:
            fh.write('family\n')
        code, _, err = run('digest', '--from', OLD, '--to', NEW, '--told', self.told, '--audience', 'family')
        self.assertEqual(code, 2)
        self.assertIn('line 1', err)

    def test_html_uses_only_inline_tags_chat_apps_take(self):
        out = self.digest('--root', 'I1', '--format', 'html', '--base-url', 'https://g.example.org', '--lang', 'es')
        self.assertIn('<b>Lena Gale (I12), prima hermana</b> <a href="https://g.example.org/person/I12">', out)
        self.assertIn("<b><u>father's side</u></b>", out)
        self.assertLessEqual(set(re.findall(r'</?([a-z0-9]+)', out)), {'b', 'u', 'i', 'a'})
        old, new = R.load(OLD), R.load(NEW)
        new['people']['hI12']['primary_name']['first_name'] = 'Lena <x> & Co'
        out = R.render(new, R.digest(old, new), 'https://g.example.org/?t=1&u=2', 'en', 'html')
        self.assertIn('Lena &lt;x&gt; &amp; Co', out)
        self.assertIn('<a href="https://g.example.org/?t=1&amp;u=2/person/I12">', out)

    def test_who_owns_what(self):
        old, new = R.load(OLD), R.load(NEW)
        # a family event belongs to both spouses; a citation on a parent link belongs to the child alone
        new['events']['hE7'] = dict(new['events']['hE1'], handle='hE7', gramps_id='E7', citation_list=[],
                                    date={'year': 1921})
        new['families']['hF3']['event_ref_list'] = [{'ref': 'hE7'}]
        new['citations']['hC7'] = dict(new['citations']['hC1'], handle='hC7', gramps_id='C7', note_list=[],
                                       media_list=[], confidence=1, page='see https://archive.example.org/b/7.')
        next(c for c in new['families']['hF3']['child_ref_list'] if c['ref'] == 'hI12')['citation_list'] = ['hC7']
        groups = R.digest(old, new)
        owners = lambda gid: sorted(g['person'] for g in groups for it in g['items'] if it['id'] == gid)
        self.assertEqual(owners('E7'), ['hI4', 'hI6'])
        self.assertEqual(owners('C7'), ['hI12'])
        item = lambda gid: next(it for g in groups for it in g['items'] if it['id'] == gid)
        self.assertIn('Baptism, 1921', item('E7')['text'])                     # a date with only a year
        self.assertEqual(item('C7')['urls'], ['https://archive.example.org/b/7'])  # sentence dot left out
        self.assertTrue(item('C7')['weak'])                                     # confidence 0 or 1 is weak
        self.assertFalse(item('C1')['weak'])

    def test_kin_on_both_sides_of_the_root(self):
        T = R.load(KIN)  # X1's parents are cousins, so Adam's line is on both of X1's sides
        old = copy.deepcopy(T)
        del old['people']['hA2']
        groups = R.digest(old, T, root='hX1')
        self.assertEqual([g['side'] for g in groups if g['person'] == 'hA2'], ['both sides'])

    def test_without_root_one_group(self):
        out = self.digest()
        self.assertIn('## other', out)
        self.assertNotIn('grandfather', out)


if __name__ == '__main__':
    unittest.main()
