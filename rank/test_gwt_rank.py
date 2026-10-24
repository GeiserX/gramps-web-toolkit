import io, json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_rank as R

GX = 'http://gedcomx.org/'
CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gwt-rank')
# Every name, place and id below is invented.


def person(pid, given, surname, principal=False, role='Principal', facts=(), ark=None):
    """A persona as the FamilySearch search JSON carries it. facts: (type, formal date, place)."""
    return {'id': pid, 'principal': principal, 'display': {'name': '%s %s' % (given, surname), 'role': role},
            'identifiers': {GX + 'Persistent': ['https://www.familysearch.org/ark:/61903/1:1:' + (ark or 'ZZ' + pid)]},
            'names': [{'nameForms': [{'fullText': '%s %s' % (given, surname),
                                      'parts': [{'type': GX + 'Given', 'value': given},
                                                {'type': GX + 'Surname', 'value': surname}]}]}],
            'facts': [{'type': GX + t, 'date': {'original': d, 'formal': '+' + d if d else ''},
                       'place': {'original': pl}} for t, d, pl in facts]}


def couple(a, b):
    return {'type': GX + 'Couple', 'person1': {'resource': '#' + a}, 'person2': {'resource': '#' + b}}


def entry(eid, persons, rels=()):
    return {'id': eid, 'score': 1.0, 'content': {'gedcomx': {'persons': persons, 'relationships': list(rels)}}}


def search(*entries):
    return {'results': len(entries), 'index': 0, 'entries': list(entries)}


def rows(doc):
    return R.gedcomx_rows(doc)


def by_id(res):
    return {r['id']: r for r in res}


class GedcomX(unittest.TestCase):
    def test_principal_with_two_agreements_is_strong_and_comes_first(self):
        doc = search(entry('A1', [person('p1', 'Ana', 'Perez Lopez', True, facts=[('Birth', '1901-05-12', 'Villazul, Murcia, Spain')], ark='A1')]),
                     entry('B2', [person('p2', 'Ana', 'Perez', True, facts=[('Birth', '1901', '')])]))
        res = R.rank(rows(doc), 'Ana', ['Perez', 'Lopez'], R.parse_facts('by=1901 bp=Villazul,_Murcia'))
        self.assertEqual([(r['id'], r['tier']) for r in res], [('A1', 'strong'), ('B2', 'possible')])
        self.assertIn('surname1+2', res[0]['why'])

    def test_first_surname_alone_matches_a_two_surname_tree_name(self):
        doc = search(entry('A1', [person('p1', 'Juan', 'Zorvalo', True, facts=[('Birth', '1880', 'Villazul'), ('Death', '1950', '')])]))
        r = R.rank(rows(doc), 'Juan', ['Zorvalo', 'Brineda'], R.parse_facts('by=1880 dy=1950'))[0]
        self.assertEqual(r['tier'], 'strong')
        r2 = R.rank(rows(doc), 'Juan', ['Brineda', 'Zorvalo'], R.parse_facts('by=1880 dy=1950'))[0]
        self.assertEqual(r2['tier'], 'possible')      # only the second surname matches: listed, never strong
        self.assertIn('surname2 only', r2['why'])

    def test_compound_given_name_matches_any_part(self):
        doc = search(entry('A1', [person('p1', 'Carmen', 'Perez', True, facts=[('Birth', '1901', 'Villazul'), ('Death', '1970', '')])]))
        r = R.rank(rows(doc), 'Maria del Carmen', ['Perez'], R.parse_facts('by=1901 dy=1970'))[0]
        self.assertEqual((r['tier'], 'given (other part)' in r['why']), ('strong', True))

    def test_name_plus_one_year_is_not_strong(self):
        doc = search(entry('A1', [person('p1', 'Jose', 'Garcia', True, facts=[('Birth', '1879', 'Spain')])]))
        r = R.rank(rows(doc), 'Jose', ['Garcia'], R.parse_facts('by=1882 bp=Villazul,_Murcia,_Spain'))[0]
        self.assertEqual(r['tier'], 'possible')
        self.assertFalse(any(w.startswith('bp') for w in r['why']))   # a country-only place never agrees

    def test_non_principal_with_an_exact_year_is_not_scored_as_the_principal(self):
        child = person('c1', 'Rosa', 'Ruiz', True, facts=[('Birth', '1901', 'Villazul')])
        father = person('f1', 'Pedro', 'Ruiz', False, 'Father', facts=[('Birth', '1870', 'Villazul')], ark='F1')
        doc = search(entry('F1', [child, father]))
        res = R.rank(rows(doc), 'Pedro', ['Ruiz'], R.parse_facts('by=1870 bp=Villazul'))
        self.assertEqual(len(res), 1)
        self.assertEqual((res[0]['name'], res[0]['role'], res[0]['tier']), ('Pedro Ruiz', 'Father', 'possible'))
        self.assertIn('matched as Father, not the principal', res[0]['why'])
        # the child's facts never count for the father
        res = R.rank(rows(doc), 'Pedro', ['Ruiz'], R.parse_facts('by=1901 bp=Villazul'))
        self.assertEqual((res[0]['by'], res[0]['tier']), (1870, 'conflict'))

    def test_spouse_tokens_match_within_one_spouse(self):
        me = person('p1', 'Ana', 'Perez', True, facts=[('Birth', '1901', '')])
        doc = search(entry('A1', [me, person('s1', 'Juan', 'Gomez', role='Spouse'), person('s2', 'Pedro', 'Ruiz', role='Spouse')],
                           [couple('p1', 's1'), couple('s2', 'p1')]))
        mixed = R.rank(rows(doc), 'Ana', ['Perez'], R.parse_facts('by=1901'), spouse='Juan Ruiz')[0]
        self.assertEqual(mixed['tier'], 'possible')
        self.assertFalse(any(w.startswith('spouse ') for w in mixed['why']))
        one = R.rank(rows(doc), 'Ana', ['Perez'], R.parse_facts('by=1901'), spouse='Pedro Ruiz')[0]
        self.assertEqual(one['tier'], 'strong')
        self.assertIn('spouse Pedro Ruiz', one['why'])
        bare = R.rank(rows(doc), 'Ana', ['Perez'], R.parse_facts('by=1901'), spouse='Juan')[0]
        self.assertEqual(bare['tier'], 'possible')
        self.assertIn('spouse needs a given name and a surname', bare['why'])

    def test_nothing_is_discarded(self):
        doc = search(entry('A1', [person('p1', 'Ana', 'Perez', True, facts=[('Birth', '1800', '')])]),
                     entry('B2', [person('p2', 'Luis', 'Soto', True)]),
                     entry('C3', [person('p3', 'Ana', 'Perez', True, facts=[('Birth', '1901', 'Villazul')])]))
        res = R.rank(rows(doc), 'Ana', ['Perez'], R.parse_facts('by=1901 bp=Villazul'))
        self.assertEqual([(r['id'], r['tier']) for r in res], [('C3', 'strong'), ('B2', 'weak'), ('A1', 'conflict')])

    def test_baptism_and_burial_stand_in_and_ranges_count(self):
        doc = search(entry('A1', [person('p1', 'Ana', 'Perez', True,
                                         facts=[('Christening', '1901-06-01', 'Villazul'), ('Burial', '1970/+1972', '')])]))
        r = R.rank(rows(doc), 'Ana', ['Perez'], R.parse_facts('by=1901 dy=1971'))[0]
        self.assertEqual((r['by'], r['dy'], r['tier']), (1901, 1970, 'strong'))

    def test_converts_to_the_normalized_record(self):
        kid = person('p1', 'Ana', 'Perez Lopez', True, facts=[('Birth', '1901', 'Villazul'), ('Burial', '1970', '')], ark='A1')
        dad = person('f1', 'Luis', 'Perez', role='Father')
        dad['gender'] = {'type': GX + 'Male'}
        pc = {'type': GX + 'ParentChild', 'person1': {'resource': '#f1'}, 'person2': {'resource': '#p1'}}
        doc = search(entry('A1', [kid, dad, person('s1', 'Juan', 'Ruiz', role='Spouse')], [pc, couple('p1', 's1')]))
        self.assertEqual(rows(doc), [{
            'source': 'familysearch', 'id': 'A1', 'name': 'Ana Perez Lopez', 'role': 'Principal', 'principal': True,
            'given': 'Ana', 'surnames': ['Perez Lopez'],
            'events': {'birth': {'year': 1901, 'year_to': 1901, 'place': 'Villazul'},
                       'death': {'year': 1970, 'year_to': 1970, 'place': ''}},
            'relatives': [{'role': 'father', 'name': 'Luis Perez'}, {'role': 'spouse', 'name': 'Juan Ruiz'}]}])

    def test_a_generic_word_in_the_tree_place_is_not_required(self):
        doc = search(entry('A1', [person('p1', 'Ana', 'Perez', True, facts=[('Birth', '1901', 'Velio, Murcia')])]))
        r = R.rank(rows(doc), 'Ana', ['Perez'], R.parse_facts('by=1901 bp=San_Velio'))[0]
        self.assertIn('bp San Velio', r['why'])
        r = R.rank(rows(doc), 'Ana', ['Perez'], R.parse_facts('by=1901 bp=San_Ortuno'))[0]
        self.assertFalse(any(w.startswith('bp') for w in r['why']))

    def test_a_fact_without_a_plain_year_does_not_crash(self):
        doc = search(entry('A1', [person('p1', 'Ana', 'Perez', True, facts=[('Birth', '1880', '')])]))
        self.assertIn('by 1880=1880', R.rank(rows(doc), 'Ana', ['Perez'], R.parse_facts('by=abt1880 dy=?'))[0]['why'])


TEXT_ES = """Resultados de la búsqueda de registros históricos (3)
Ana Perez Lopez

Persona principal
España, Registro Civil, 1870-1950
Nacimiento  12 mayo 1901
Villazul, Murcia, España
Cónyuge
Juan Ruiz
Ana Perez

Principal
Bautismos 1850-1900
Bautismo  1903
Residencia 1930
Ana Maria Perez

Cónyuge
España, Matrimonios
Matrimonio  1925
"""


class PageText(unittest.TestCase):
    def test_spanish_page(self):
        rs = R.text_rows(TEXT_ES)
        self.assertEqual([r['name'] for r in rs], ['Ana Perez Lopez', 'Ana Perez', 'Ana Maria Perez'])
        self.assertEqual(rs[0]['events'], {'birth': {'year': 1901, 'year_to': 1901, 'place': 'Villazul, Murcia, España'}})
        self.assertEqual(rs[0]['relatives'], [{'role': 'spouse', 'name': 'Juan Ruiz'}])
        self.assertEqual(rs[1]['events'], {'birth': {'year': 1903, 'year_to': 1903, 'place': ''}})   # the title's 1850 is not an event; Residencia is no place
        self.assertEqual([r['principal'] for r in rs], [True, True, False])
        res = R.rank(rs, 'Ana', ['Perez', 'Lopez'], R.parse_facts('by=1901 bp=Villazul'), spouse='Juan Ruiz')
        self.assertEqual([r['tier'] for r in res], ['strong', 'possible', 'weak'])

    def test_english_page_and_surname_split(self):
        page = "Antoine Le Chat\n\nPrincipal\nFrance, Births\nBirth  1861\nVillebleu\nSpouses Rose Bellanger, Marie Roux\n"
        r = R.rank(R.text_rows(page), 'Antonin', ['Lechat'], R.parse_facts('by=1860 bp=Villebleu'), spouse='Rose Roux')[0]
        self.assertEqual(r['tier'], 'possible')       # a given-name variant is never strong
        self.assertIn('given variant', r['why'])
        self.assertFalse(any(w.startswith('spouse ') for w in r['why']))


class FullNameOnly(unittest.TestCase):
    def test_a_surname_token_is_never_the_given_name(self):
        page = "Juan Garcia Martin\n\nPrincipal\nSpain\nBirth  1901\nVillazul\nDeath  1970\n"
        r = R.rank(R.text_rows(page), 'Martin', ['Garcia'], R.parse_facts('by=1901 bp=Villazul dy=1970'))[0]
        self.assertEqual(r['tier'], 'weak')
        self.assertIn('given differs', r['why'])
        r = R.rank(R.text_rows(page), 'Juan', ['Garcia'], R.parse_facts('by=1901 bp=Villazul dy=1970'))[0]
        self.assertEqual(r['tier'], 'strong')
        rec = {'candidates': [{'name': 'Juan Garcia Martin', 'principal': True, 'given': None, 'surnames': None,
                               'events': {'birth': {'year': 1901, 'place': 'Villazul'}, 'death': {'year': 1970}}}]}
        r = R.rank(R.normalized_rows(rec), 'Martin', ['Garcia'], R.parse_facts('by=1901 bp=Villazul dy=1970'))[0]
        self.assertEqual(r['tier'], 'weak')


class Normalized(unittest.TestCase):
    RECS = {'candidates': [
        {'source': 'othersite', 'id': 'r1', 'name': 'Ana Perez', 'role': 'Principal', 'principal': True,
         'given': 'Ana', 'surnames': ['Perez', 'Lopez'], 'events': {'birth': {'year': 1901, 'place': 'Villazul, Spain'}},
         'relatives': [{'role': 'spouse', 'name': 'Juan Ruiz'}, {'role': 'father', 'name': 'Juan Ruiz'}]},
        {'source': 'othersite', 'id': 'r2', 'name': 'Ana Perez', 'events': {}}]}

    def test_another_source_feeds_the_same_scorer(self):
        res = R.rank(R.normalized_rows(self.RECS), 'Ana', ['Perez'], R.parse_facts('by=1901'), spouse='Juan Ruiz')
        self.assertEqual([(r['id'], r['tier'], r['source']) for r in res], [('r1', 'strong', 'othersite'), ('r2', 'weak', 'othersite')])

    def test_only_a_spouse_relative_counts_as_spouse(self):
        recs = json.loads(json.dumps(self.RECS))
        recs['candidates'][0]['relatives'] = [{'role': 'father', 'name': 'Juan Ruiz'}]
        r = R.rank(R.normalized_rows(recs), 'Ana', ['Perez'], R.parse_facts('by=1901'), spouse='Juan Ruiz')[0]
        self.assertEqual(r['tier'], 'possible')

    def test_a_bad_record_is_an_error_not_a_row(self):
        for bad in ({'events': {}}, {'name': 'Ana Perez', 'surnames': 'Perez'}, {'name': 'Ana', 'given': 5},
                    {'name': 'Ana', 'relatives': ['Juan']}, {'name': 'Ana', 'events': {'birth': {'year': '1901'}}},
                    {'name': 'Ana', 'events': {'birth': {'place': 3}}}):
            with self.assertRaises(ValueError, msg=bad):
                R.normalized_rows({'candidates': [bad]})


class Cli(unittest.TestCase):
    def run_cli(self, body, *args, stdin=False):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'page')
            with open(path, 'w') as f:
                f.write(body)
            target = '-' if stdin else path
            return subprocess.run([sys.executable, CLI, target, *args], capture_output=True, text=True,
                                  input=body if stdin else None)

    def test_exit_codes(self):
        doc = json.dumps(search(entry('A1', [person('p1', 'Ana', 'Perez', True, facts=[('Birth', '1901', 'Villazul')])])))
        ok = self.run_cli(doc, '--given', 'Ana', '--surname', 'Perez', '--facts', 'by=1901 bp=Villazul')
        self.assertEqual(ok.returncode, 0)
        self.assertTrue(ok.stdout.startswith('strong\t'))
        partial = self.run_cli(doc, '--given', 'Ana', '--surname', 'Perez', '--facts', 'by=1700', stdin=True)
        self.assertEqual(partial.returncode, 1)
        self.assertTrue(partial.stdout.startswith('conflict\t'))   # printed, not hidden
        self.assertEqual(self.run_cli(json.dumps(search()), '--given', 'Ana').returncode, 2)
        self.assertEqual(self.run_cli('{"persons": []}', '--given', 'Ana').returncode, 2)
        self.assertEqual(self.run_cli('nothing here', '--given', 'Ana').returncode, 2)
        recs = json.dumps(Normalized.RECS)
        self.assertEqual(self.run_cli(recs, '--given', 'Ana', '--surname', 'Perez', '--facts', 'by=1901 bp=Villazul').returncode, 0)
        self.assertEqual(self.run_cli('{"candidates": [{"events": {}}]}', '--given', 'Ana').returncode, 2)

    def test_malformed_input_exits_2_never_1(self):
        bad_fact = search(entry('A1', [person('p1', 'Ana', 'Perez', True)]))
        bad_fact['entries'][0]['content']['gedcomx']['persons'][0]['facts'] = [{'type': GX + 'Birth', 'place': 'Villazul'}]
        num_name = search(entry('A1', [person('p1', 'Ana', 'Perez', True)]))
        num_name['entries'][0]['content']['gedcomx']['persons'][0]['names'][0]['nameForms'][0]['fullText'] = 5
        for body in ('{"entries": ["x"]}', json.dumps(bad_fact), json.dumps(num_name),
                     '{"candidates": [{"name": "Ana", "given": 5}]}', '{"candidates": [{"name": "Ana", "relatives": ["Juan"]}]}',
                     '{"candidates": [{"name": "Ana", "surnames": "Perez"}]}'):
            res = self.run_cli(body, '--given', 'Ana', '--surname', 'Perez')
            self.assertEqual(res.returncode, 2, (body, res.stderr))
            self.assertNotIn('Traceback', res.stderr)

    def test_tsv_fields_stay_on_one_line(self):
        recs = {'candidates': [{'id': 'r1\tx', 'name': 'Ana\nPerez', 'role': 'Principal\r', 'principal': True,
                                'events': {'birth': {'year': 1901, 'place': 'Villa\tazul'}}}]}
        out = self.run_cli(json.dumps(recs), '--given', 'Ana', '--surname', 'Perez').stdout
        self.assertEqual(out.count('\n'), 1)
        self.assertEqual(len(out.rstrip('\n').split('\t')), 10)


if __name__ == '__main__':
    unittest.main()
