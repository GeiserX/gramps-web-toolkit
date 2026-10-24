import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_rank as R

PAGE = """Historical Record Search Results (3)
Ana Perez Lopez

Principal
Spain, Civil Registration, 1870-1950
Birth  12 May 1901
Madrid, Spain
Death  1970
Ana Perez Lopez

Principal
Mexico, Census, 1930
Census  1930
Puebla, Mexico
Birth  1850
Ana Maria Perez

Spouse
Spain, Marriages, 1900-1920
Marriage  1925
Madrid
"""


class T(unittest.TestCase):
    def test_parses_records(self):
        recs = R.records(PAGE)
        self.assertEqual([r['name'] for r in recs], ['Ana Perez Lopez', 'Ana Perez Lopez', 'Ana Maria Perez'])
        self.assertEqual(recs[0]['by'], 1901)
        self.assertEqual(recs[0]['dy'], 1970)

    def test_year_and_place_pick_the_right_row(self):
        res = R.rank(PAGE, 'Ana Perez', R.parse_facts('by=1901 bp=Madrid dy=1970'))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]['by'], 1901)
        self.assertGreaterEqual(res[0]['score'], 5)

    def test_wrong_year_is_not_strong_and_non_principal_is_skipped(self):
        res = R.rank(PAGE, 'Ana Perez', R.parse_facts('by=1850'))
        self.assertEqual([r['by'] for r in res], [1850])
        self.assertEqual(R.rank(PAGE, 'Ana Perez', R.parse_facts('by=1700')), [])

    def test_spelling_variants_still_count(self):
        page = "Antoine Ovide Le Chat\n\nPrincipal\nFrance, Births\nBirth  1861\nTours\n"
        res = R.rank(page, 'Antonin Lechat', R.parse_facts('by=1860 bp=Tours'))
        self.assertEqual(len(res), 1)
        self.assertIn('given name variant', res[0]['why'])

    def test_surname_must_be_a_whole_token_or_an_exact_split(self):
        page = "Ana Lopez\n\nPrincipal\nSpain\nBirth  1901\nMadrid\n"
        self.assertEqual(R.rank(page, 'Ana Pe', R.parse_facts('by=1901 bp=Madrid')), [])
        self.assertEqual(R.rank(page, 'Ana Lop', R.parse_facts('by=1901 bp=Madrid')), [])
        page2 = "Ana Le Pen\n\nPrincipal\nFrance\nBirth  1901\nTours\n"
        self.assertEqual(len(R.rank(page2, 'Ana Lepen', R.parse_facts('by=1901 bp=Tours'))), 1)
        self.assertEqual(R.rank(page2, 'Ana Epen', R.parse_facts('by=1901 bp=Tours')), [])

    def test_a_contradicting_year_vetoes_even_with_everything_else_matching(self):
        page = ("Ana Perez\n\nPrincipal\nSpain\nBirth  1800\nMadrid\nDeath  1970\nMadrid\n"
                "Parents Pedro Gomez, Rosa Soto\nSpouse Juan Ruiz\n")
        facts = R.parse_facts('by=1901 bp=Madrid dy=1970 dp=Madrid')
        self.assertEqual(R.rank(page, 'Ana Perez', facts, spouse='Juan Ruiz'), [])
        res = R.rank(page, 'Ana Perez', R.parse_facts('by=1800 bp=Madrid dy=1970'))
        self.assertEqual(len(res), 1)
        self.assertEqual(R.rank(page, 'Ana Perez', R.parse_facts('dy=1900 bp=Madrid')), [])

    def test_multiline_events(self):
        page = ("Ana Perez\n\nPrincipal\nSpain, Civil Registration\n\nBirth\n1901\nMadrid\n\nDeath\n1970\nMadrid\n")
        rec = R.records(page)[0]
        self.assertEqual((rec['by'], rec['dy']), (1901, 1970))
        self.assertEqual(rec['places'], ['Madrid', 'Madrid'])
        self.assertEqual(len(R.rank(page, 'Ana Perez', R.parse_facts('by=1901 bp=Madrid'))), 1)

    def test_records_with_no_readable_event_are_unreadable_not_a_negative(self):
        page = "Ana Perez\n\nPrincipal\nSpain, Civil Registration\nSomething else\n"
        self.assertIsNone(R.rank(page, 'Ana Perez', R.parse_facts('by=1901')))

    def test_place_needs_more_than_a_generic_shared_token(self):
        for row, want in (('San Pedro', 'San Jose'), ('Saint Pierre', 'Saint Jean'), ('La Rochelle', 'La Coruna'), ('New York', 'New Orleans')):
            page = "Ana Perez\n\nPrincipal\nSpain\nBirth  1901\n%s\n" % row
            res = R.rank(page, 'Ana Perez', R.parse_facts('by=1901 bp=%s' % want.replace(' ', '_')))
            self.assertFalse(any('bp ' in w for r in res for w in r['why']), (row, want))
        page = "Ana Perez\n\nPrincipal\nSpain\nBirth  1901\nSan Jose, Cadiz\n"
        res = R.rank(page, 'Ana Perez', R.parse_facts('by=1901 bp=San_Jose'))
        self.assertIn('bp San Jose', res[0]['why'])

    def test_places_belong_to_their_own_event(self):
        page = "Ana Perez\n\nPrincipal\nSpain\nBirth  1901\nMadrid\nDeath  1970\nParis\n"
        facts = R.parse_facts('by=1901 bp=Paris')       # the tree says born in Paris; the row says DIED in Paris
        res = R.rank(page, 'Ana Perez', facts)
        self.assertTrue(res == [] or not any('bp Paris' in r['why'] for r in res))
        self.assertEqual([r['score'] for r in res], [4])   # name + exact year only: no place point for the wrong event
        ok = R.rank(page, 'Ana Perez', R.parse_facts('by=1901 bp=Madrid dp=Paris'))
        self.assertIn('bp Madrid', ok[0]['why'])
        self.assertIn('dp Paris', ok[0]['why'])

    def test_spouse_needs_a_spouse_line_not_parents(self):
        page = "Ana Perez\n\nPrincipal\nSpain\nBirth  1901\nMadrid\nParents Ana Gomez, Pedro Perez\n"
        res = R.rank(page, 'Ana Perez', R.parse_facts('by=1901'), spouse='Ana Gomez')
        self.assertTrue(all('spouse' not in r['why'] for r in res))
        self.assertEqual([r['score'] for r in res], [4])      # surname, given name, exact year: no spouse points
        page2 = page.replace('Parents Ana Gomez, Pedro Perez', 'Spouses Ana Gomez')
        res2 = R.rank(page2, 'Ana Perez', R.parse_facts('by=1901'), spouse='Ana Gomez')
        self.assertIn('spouse', res2[0]['why'])

    def test_accents_and_missing_surname(self):
        self.assertEqual(R.norm('José Pérez'), ['jose', 'perez'])
        self.assertEqual(R.rank(PAGE, 'Ana Gomez', {}), [])

    def test_unparseable_page_is_not_a_negative(self):
        self.assertIsNone(R.rank('Historical Record Search Results (0)\n', 'Ana Perez', {}))
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write('nothing here')
        code = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gwt-rank'),
                               f.name, '--name', 'Ana Perez'], capture_output=True).returncode
        os.unlink(f.name)
        self.assertEqual(code, 2)

    def test_cli_exit_codes(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write(PAGE)
        cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gwt-rank')
        ok = subprocess.run([cli, f.name, '--name', 'Ana Perez', '--facts', 'by=1901'], capture_output=True, text=True)
        none = subprocess.run([cli, f.name, '--name', 'Ana Perez', '--facts', 'by=1700'], capture_output=True, text=True)
        os.unlink(f.name)
        self.assertEqual((ok.returncode, none.returncode), (0, 1))
        self.assertIn('1901', ok.stdout)


if __name__ == '__main__':
    unittest.main()
