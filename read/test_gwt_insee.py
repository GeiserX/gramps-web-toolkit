"""Tests for `gwt-read insee`. Invented fixed-width rows in fixtures/insee-deces-1985.txt and built here; the reader
reads local files only, and the tests check it never asks for a gate, a cache or the network.
Run: cd read && python3 -m unittest -v test_gwt_insee"""
import contextlib, glob, io, json, os, tempfile, unittest
from unittest import mock

import gwt_insee  # noqa: E402  (test_gwt_read is not needed: this source has no transport)
import gwt_read  # noqa: E402
import gwt_rank  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'insee-deces-1985.txt')


def line(name='LORIOT*PAUL/', sex='1', bd='19200101', bcode='64990', btown='BRUNVILLE', bcountry='', dd='19850101',
         dcode='64990', act='0000001', width=198):
    """One line in INSEE's layout: name 80, sex 1, birth date 8, code 5, commune 30, country 30, death date 8, code 5,
    act 9, then filler."""
    s = name.ljust(80) + sex + bd + bcode.ljust(5) + btown.ljust(30) + bcountry.ljust(30) + dd + dcode.ljust(5) + act.ljust(9)
    return s.ljust(width)[:width] if width >= 176 else s[:width]


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, 'out')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, *lines, raw=None):
        p = os.path.join(self.tmp.name, name)
        with open(p, 'wb') as f:
            f.write(raw if raw is not None else ('\n'.join(lines) + '\n').encode('ascii'))
        return p

    def run_insee(self, files, surnames):
        o, e = io.StringIO(), io.StringIO()
        argv = ['insee', 'search', '--files'] + list(files) + sum([['--surname', s] for s in surnames], []) + \
            ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if '\t' in l)
        return code, lines, o.getvalue() + e.getvalue()

    def cands(self, lines):
        with open(lines['candidates']) as f:
            return json.load(f)['candidates']

    def written(self):
        return glob.glob(os.path.join(self.out, '*'))


class Candidates(Run):
    def test_a_line_is_a_principal_with_birth_year_and_commune_as_written_and_the_death(self):
        code, lines, _ = self.run_insee([FIX], ['Ortega'])
        self.assertEqual(code, 0)
        self.assertEqual(lines['source'], 'insee')
        c = self.cands(lines)[0]
        self.assertTrue(c['principal'])
        self.assertEqual((c['name'], c['given'], c['surnames'], c['sex']), ('TOMAS ORTEGA', 'TOMAS', ['ORTEGA'], 'M'))
        self.assertEqual(c['events'], {'birth': {'year': 1921, 'year_to': 1921, 'place': 'VILLAZUL, ESPAGNE'},
                                       'death': {'year': 1985, 'year_to': 1985, 'place': '64999'}})
        self.assertEqual(c['id'], '19850702-64999-0000123')

    def test_full_dates_codes_and_act_stay_as_fields_gwt_rank_does_not_score(self):
        code, lines, _ = self.run_insee([FIX], ['Ortega'])
        c = self.cands(lines)[0]
        self.assertEqual((c['birth_date'], c['death_date'], c['birth_place_code'], c['death_place_code'], c['act']),
                         ('19210314', '19850702', '99134', '64999', '0000123'))
        self.assertEqual((c['birth_commune'], c['birth_country'], c['file'], c['line']),
                         ('VILLAZUL', 'ESPAGNE', 'insee-deces-1985.txt', 1))

    def test_a_birth_in_france_has_the_commune_alone(self):
        code, lines, _ = self.run_insee([FIX], ['Duval'])
        self.assertEqual(self.cands(lines)[0]['events']['birth']['place'], 'LESPIAC-SUR-ORBE')

    def test_surname_matches_whole_words_only(self):
        code, lines, _ = self.run_insee([FIX], ['Ortega'])
        names = [c['name'] for c in self.cands(lines)]
        self.assertEqual(names, ['TOMAS ORTEGA', 'MARIA ROSA ORTEGA SANZ'])    # never ORTEGAS
        self.assertFalse(gwt_insee.surname_matches('ORTEGAS', [['ortega']]))
        self.assertTrue(gwt_insee.surname_matches('DE LA FONTAINE', [['la', 'fontaine']]))

    def test_surname_is_folded_for_case_and_accents(self):
        code, lines, _ = self.run_insee([FIX], ['ortéga'])
        self.assertEqual(len(self.cands(lines)), 2)

    def test_surname_repeats_and_one_pass_serves_a_branch(self):
        code, lines, out = self.run_insee([FIX], ['Ortega', 'Sanz'])
        self.assertEqual([c['line'] for c in self.cands(lines)], [1, 4, 5])   # ORTEGA SANZ once, not twice
        self.assertIn('results\t3 of 6 lines in 1 file', out)

    def test_an_unknown_year_is_no_year_and_the_date_is_kept_as_written(self):
        code, lines, _ = self.run_insee([FIX], ['Sanz'])
        c = [c for c in self.cands(lines) if c['name'] == 'PILAR SANZ'][0]
        self.assertEqual(c['events']['birth']['year'], None)
        self.assertEqual((c['birth_date'], c['death_date']), ('00000000', '19850900'))
        self.assertEqual(c['events']['death']['year'], 1985)

    def test_a_calendar_date_that_never_existed_is_kept_not_refused(self):
        # INSEE's own files hold 29 February of common years; the date is kept as written
        code, lines, _ = self.run_insee([FIX], ['Ferrand'])
        self.assertEqual(code, 0)
        self.assertEqual(self.cands(lines)[0]['birth_date'], '18980229')

    def test_a_cp850_byte_is_read_as_cp850_and_marked(self):
        code, lines, _ = self.run_insee([FIX], ['Sanz'])
        c = [c for c in self.cands(lines) if c['name'] == 'PILAR SANZ'][0]
        self.assertEqual(c['birth_commune'], 'VILLENEUVE-LES-PRES 2°')
        self.assertEqual(c['encoding'], 'cp850')
        self.assertNotIn('encoding', self.cands(lines)[0])

    def test_a_utf8_line_is_read_as_utf8(self):
        p = self.write('u.txt', raw=(line(btown='VILLAZUL-ÉTANG') + '\n').encode('utf-8'))
        code, lines, _ = self.run_insee([p], ['Loriot'])
        c = self.cands(lines)[0]
        self.assertEqual(c['birth_commune'], 'VILLAZUL-ÉTANG')
        self.assertNotIn('encoding', c)

    def test_a_latin1_line_is_read_as_latin1_and_marked(self):
        # some years mix Latin-1 lines with CP850 ones: 0xF1 is ñ in Latin-1, '±' in CP850
        p = self.write('l.txt', raw=(line(btown='VILLAZU\xf1A', bcountry='ESPAGNE') + '\n').encode('latin-1'))
        code, lines, _ = self.run_insee([p], ['Loriot'])
        c = self.cands(lines)[0]
        self.assertEqual((c['birth_commune'], c['encoding']), ('VILLAZUñA', 'latin-1'))

    def test_a_latin1_degree_sign_is_not_read_as_a_cp850_box(self):
        p = self.write('d.txt', raw=(line(btown='VILLENEUVE 3\xb0') + '\n').encode('latin-1'))
        code, lines, _ = self.run_insee([p], ['Loriot'])
        self.assertEqual(self.cands(lines)[0]['birth_commune'], 'VILLENEUVE 3°')

    def test_a_latin1_birthplace_still_scores_in_gwt_rank(self):
        p = self.write('r.txt', raw=(line(name='ORTEGA*TOMAS/', bd='19210314', btown='VILLAZU\xd1A') + '\n')
                       .encode('latin-1'))
        code, lines, _ = self.run_insee([p], ['Ortega'])
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            gwt_rank.main([lines['candidates'], '--given', 'Tomás', '--surname', 'Ortega',
                           '--facts', 'by=1921 bp=Villazuña', '--format', 'json'])
        self.assertEqual(json.loads(o.getvalue().splitlines()[0])['tier'], 'strong')

    def test_columns_count_characters_not_bytes_on_a_utf8_line(self):
        # recent files carry a few UTF-8 lines; a two-byte character must not shift the death date or act
        p = self.write('c.txt', raw=(line(btown='VILLAZUL 4°', bcountry='ÉTANG', dd='19850705', act='Décès 7')
                                     + '\n').encode('utf-8'))
        code, lines, _ = self.run_insee([p], ['Loriot'])
        c = self.cands(lines)[0]
        self.assertEqual((c['death_date'], c['death_place_code'], c['act']), ('19850705', '64990', 'Décès 7'))

    def test_several_files_in_one_pass(self):
        p = self.write('deces-1986.txt', line(name='ORTEGA*LUIS/', dd='19860305'))
        code, lines, out = self.run_insee([FIX, p], ['Ortega'])
        self.assertEqual([(c['file'], c['line']) for c in self.cands(lines)],
                         [('insee-deces-1985.txt', 1), ('insee-deces-1985.txt', 4), ('deces-1986.txt', 1)])
        self.assertIn('3 of 7 lines in 2 files', out)
        with open(lines['evidence']) as f:
            env = json.load(f)
        self.assertEqual([x['file'] for x in env['files']], ['insee-deces-1985.txt', 'deces-1986.txt'])

    def test_blank_lines_are_not_rows(self):
        p = self.write('b.txt', raw=(line() + '\r\n\r\n' + line(name='LORIOT*ANNE/', sex='2') + '\r\n').encode('ascii'))
        code, lines, _ = self.run_insee([p], ['Loriot'])
        self.assertEqual([c['line'] for c in self.cands(lines)], [1, 3])

    def test_no_match_exits_2(self):
        code, lines, _ = self.run_insee([FIX], ['Zorvalo'])
        self.assertEqual(code, 2)
        self.assertEqual(self.cands(lines), [])

    def test_no_surname_is_a_usage_error(self):
        code, _, out = self.run_insee([FIX], [])
        self.assertEqual(code, 64)
        self.assertEqual(self.written(), [])

    def test_offline_no_gate_no_cache_no_network(self):
        boom = mock.Mock(side_effect=AssertionError('network, gate or cache used'))
        with mock.patch.object(gwt_read, 'Gate', boom), mock.patch.object(gwt_read, 'FSCache', boom), \
                mock.patch('urllib.request.urlopen', boom), mock.patch('socket.socket', boom):
            code, lines, _ = self.run_insee([FIX], ['Ortega'])
        self.assertEqual(code, 0)
        boom.assert_not_called()


class Malformed(Run):
    """A line out of the layout stops the run with exit 2 and nothing written, even a line no --surname matches."""
    def assert_stops(self, bad, why):
        p = self.write('deces-bad.txt', line(name='ORTEGA*ANA/'), bad, line(name='ORTEGA*JUAN/'))
        code, lines, out = self.run_insee([p], ['Ortega'])
        self.assertEqual(code, 2, out)
        self.assertIn('deces-bad.txt line 2', out)
        self.assertIn(why, out)
        self.assertIn('nothing was written', out)
        self.assertEqual(self.written(), [])

    def test_letters_in_a_birth_date(self):
        self.assert_stops(line(bd='1921O314'), "birth date: date '1921O314' is not AAAAMMJJ")

    def test_letters_in_a_death_date(self):
        self.assert_stops(line(dd='1985-7-2'), "death date: date '1985-7-2' is not AAAAMMJJ")

    def test_month_13(self):
        self.assert_stops(line(bd='19211314'), 'month 13')

    def test_day_32(self):
        self.assert_stops(line(dd='19850732'), 'day 32')

    def test_a_line_too_short_for_the_death_date(self):
        self.assert_stops(line(width=150), 'line is 150 characters')

    def test_a_name_without_the_star(self):
        self.assert_stops(line(name='LORIOT PAUL/'), 'has no "*"')

    def test_a_sex_that_is_not_1_or_2(self):
        self.assert_stops(line(sex='M'), "sex 'M' is not 1 or 2")

    def test_a_shifted_line_is_caught(self):
        self.assert_stops(' ' + line()[:-1], "sex ' ' is not 1 or 2")

    def test_a_missing_file_exits_2_and_writes_nothing(self):
        code, _, out = self.run_insee([FIX, os.path.join(self.tmp.name, 'nope.txt')], ['Ortega'])
        self.assertEqual(code, 2)
        self.assertIn('cannot read', out)
        self.assertEqual(self.written(), [])

    def test_the_code_and_act_may_be_cut_short(self):
        p = self.write('short.txt', line(width=176).rstrip())
        code, lines, _ = self.run_insee([p], ['Loriot'])
        self.assertEqual(code, 0)
        self.assertEqual(self.cands(lines)[0]['act'], '0000001')


class EndToEnd(Run):
    def test_gwt_rank_reads_the_candidates_and_finds_the_strong_row(self):
        code, lines, _ = self.run_insee([FIX], ['Ortega', 'Sanz'])
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            rc = gwt_rank.main([lines['candidates'], '--given', 'Tomás', '--surname', 'Ortega',
                                '--facts', 'by=1921 bp=Villazul', '--format', 'json'])
        rows = [json.loads(x) for x in o.getvalue().splitlines() if x.strip()]
        self.assertEqual(rc, 0)
        self.assertEqual([(r['tier'], r['name']) for r in rows][0], ('strong', 'TOMAS ORTEGA'))
        self.assertEqual(sum(r['tier'] == 'strong' for r in rows), 1)


if __name__ == '__main__':
    unittest.main()
