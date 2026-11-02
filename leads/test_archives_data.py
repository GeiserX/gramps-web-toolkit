"""The shipped catalogue (leads/archives/<CC>.tsv) is valid, and the checks that say so can fail."""
import datetime, os, re, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_leads as L

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'archives')
HEAD = '\t'.join(L.CATALOGUE_COLUMNS)
GOOD = ('Exampleland|Examplia\tcountry\tInvented national archive\tCivil registration\tbirth,death\t1871-\t1871\t'
        'https://archives.example.org/?q={surname}&y={year_from}\tonline\tok\t\t2026-11-02\tinvented')


class ShippedCatalogueTest(unittest.TestCase):
    def test_every_file_is_a_country_code_and_reads_cleanly(self):
        files = sorted(os.listdir(DIR))
        self.assertGreater(len(files), 50)
        for name in files:
            self.assertRegex(name, r'^[A-Z]{2}\.tsv$')
            rows = L.read_catalogue(os.path.join(DIR, name))  # columns, enums, years, placeholders: BadInput if not
            self.assertTrue(rows, name)
            for row in rows:
                self.assertTrue(row['url'] or row['notes'], '%s line %d: no url and no note' % (name, row['line']))
                if row['tested']:
                    self.assertLessEqual(row['tested'], datetime.date.today().isoformat())

    def test_builtin_all_loads_every_file_and_country_files_hold_their_country(self):
        self.assertEqual(L.catalogue_codes(DIR)[-1], 'XX')  # several-country rows come last
        index = L.load_catalogue('builtin:all', DIR)
        for code in ('ES', 'FR', 'IT', 'DE', 'GB', 'US', 'NL', 'SE', 'PL', 'MX', 'AR', 'AU'):
            rows = L.read_catalogue(os.path.join(DIR, code + '.tsv'))
            self.assertTrue(any(r['match_level'] == 'country' for r in rows), code)
        self.assertIn('*', index)  # the world rows
        self.assertIn('espana', index)

    def test_every_url_placeholder_fills_and_stays_one_url(self):
        values = dict(L.PROBE_VALUES, surname='Smith & Co', place='Saint/Exemple')
        for code in L.catalogue_codes(DIR):
            for row in L.read_catalogue(os.path.join(DIR, code + '.tsv')):
                url = L.fill_url(row['url'], values)
                self.assertNotIn(' ', url)
                self.assertNotIn('{', url.replace('%7B', ''))

    @unittest.skipUnless(os.path.isfile(os.path.join(DIR, '..', '..', 'docs', 'archives.md')), 'no docs folder')
    def test_the_docs_table_lists_every_file_with_its_row_count(self):
        with open(os.path.join(DIR, '..', '..', 'docs', 'archives.md'), encoding='utf-8') as f:
            rows = [re.fullmatch(r'\| `([a-z]{2})` \| [^|]+ \| (\d+) \|\n?', line) for line in f]
        table = {m.group(1): int(m.group(2)) for m in rows if m}
        want = {c.lower(): len(L.read_catalogue(os.path.join(DIR, c + '.tsv'))) for c in L.catalogue_codes(DIR)}
        self.assertEqual(table, want)


class CatalogueCheckTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td)

    def write(self, *lines, header=HEAD, name='ZZ.tsv'):
        path = os.path.join(self.td, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join((header,) + lines) + '\n')
        return path

    def test_a_good_row_reads(self):
        row = L.read_catalogue(self.write(GOOD))[0]
        self.assertEqual(row['names'], ['exampleland', 'examplia'])
        self.assertEqual(row['years'], [(1871, 9999)])  # an open end runs to today
        self.assertEqual(row['record_types'], {'birth', 'death'})

    def test_each_bad_value_stops_with_its_line(self):
        def cell(col, value):
            cells = GOOD.split('\t')
            cells[L.CATALOGUE_COLUMNS.index(col)] = value
            return '\t'.join(cells)
        bad = {
            'unknown access': cell('access', 'maybe'),
            'unknown robots': cell('robots', 'perhaps'),
            'unknown placeholder': cell('url', 'https://x.example/?q={name}'),
            'placeholder with a format': cell('url', 'https://x.example/?q={surname!r}'),
            'not a web address': cell('url', 'ftp://x.example/'),
            'backwards years': cell('years', '1900-1800'),
            'years as words': cell('years', 'all'),
            'unknown record type': cell('record_types', 'birth,gossip'),
            'repeated record type': cell('record_types', 'birth,birth'),
            'empty archive': cell('archive', ''),
            'civil_start not a year': cell('civil_start', '1870s'),
            'tested not a date': cell('tested', '02/11/2026'),
            'match level unknown': cell('match_level', 'continent'),
            'world level without *': cell('match_level', 'world'),
            '* without world level': cell('match', '*'),
            'alias repeated once folded': cell('match', 'Examplia|EXAMPLÍA'),
            'empty alias': cell('match', 'Examplia||Other'),
            'padded cell': cell('series', ' Civil registration'),
            'extra cell': GOOD + '\tx',
            'missing cell': GOOD.rsplit('\t', 1)[0],
        }
        for what, line in bad.items():
            with self.subTest(what):
                with self.assertRaisesRegex(L.BadInput, 'line 2'):
                    L.read_catalogue(self.write(line))
        with self.assertRaisesRegex(L.BadInput, 'header'):
            L.read_catalogue(self.write(GOOD, header=HEAD.replace('robots', 'robot')))
        with self.assertRaisesRegex(L.BadInput, 'header'):
            L.read_catalogue(self.write(GOOD, header='\t'.join(reversed(L.CATALOGUE_COLUMNS))))

    def test_an_unknown_code_names_the_codes_there_are(self):
        self.write(GOOD)
        with self.assertRaisesRegex(L.BadInput, 'no built-in catalogue QQ; there are zz'):
            L.load_catalogue('builtin:zz,qq', self.td)
        with self.assertRaisesRegex(L.BadInput, r'\(none named\)'):
            L.load_catalogue('builtin:', self.td)
        self.assertIn('examplia', L.load_catalogue('builtin:ZZ', self.td))


if __name__ == '__main__':
    unittest.main()
