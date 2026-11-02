"""Tests for gwt-geocode. An invented tree, recorded-shape provider answers with invented places, a fake transport:
no request ever leaves this machine. Run: cd geocode && python3 -m pytest -q"""
import contextlib, io, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'fetch'))

import gwt_geocode as G  # noqa: E402

FIX = os.path.join(HERE, 'fixtures')
TREE = os.path.join(FIX, 'tree.json')
DUMP = os.path.join(FIX, 'geonames-excerpt.txt')
with open(os.path.join(FIX, 'responses.json'), encoding='utf-8') as _f:
    RESP = json.load(_f)


class FakeHttp:
    """Answers from responses.json by host and query; anything else is an empty answer. Records every call."""

    def __init__(self):
        self.calls = []

    def get_json(self, url, params=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        if 'nominatim' in url:
            return RESP['nominatim'].get(params.get('q'), [])
        if 'api-adresse' in url:
            return RESP['ban'].get(params.get('q'), {'features': []})
        if url.endswith('/candidates'):
            return RESP['carto_candidates'].get(params.get('q'), [])
        if url.endswith('/find'):
            return RESP['carto_find'][params['id']]
        raise AssertionError('unexpected url ' + url)


def run(*extra, overrides=None):
    """(exit code, report rows by gramps_id, ops by place, fake http)."""
    http = FakeHttp()
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'changes.jsonl')
        argv = ['--snapshot', TREE, '--out', out, '--geonames', DUMP, '--no-cache'] + list(extra)
        if overrides is not None:
            ov = os.path.join(d, 'overrides.tsv')
            with open(ov, 'w', encoding='utf-8') as f:
                f.write(overrides)
            argv += ['--overrides', ov]
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = G.main(argv, http=http)
        ops = {}
        if os.path.exists(out):
            with open(out, encoding='utf-8') as f:
                for line in f:
                    op = json.loads(line)
                    ops[op['place']] = op
    lines = buf.getvalue().splitlines()
    rows = {}
    if lines:
        head = lines[0].split('\t')
        for line in lines[1:]:
            r = dict(zip(head, line.split('\t')))
            rows[r['gramps_id']] = r
    return code, rows, ops, http, err.getvalue()


OVERRIDE = 'gramps_id\tlat\tlong\tnote\nP9006\t40.005\t-3.005\tthe old cemetery, from a 1900 town plan\n'


class Spots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.rows, cls.ops, cls.http, cls.err = run(overrides=OVERRIDE)

    def test_a_church_15_km_off_falls_back_to_the_town(self):
        r = self.rows['P9004']
        self.assertEqual(r['match'], 'town-centroid')
        self.assertEqual((r['lat'], r['long']), ('40.000000', '-3.000000'))
        self.assertIn('15.0 km from Villaejemplo, over the 5 km limit', r['note'])
        self.assertEqual(self.ops['P9004']['set'], {'lat': 40.0, 'long': -3.0})

    def test_a_parish_never_matches_a_modern_namesake(self):
        # «Parroquia de San Ejemplo Obrero» is 0.8 km away, but it is not «Iglesia de San Ejemplo»
        self.assertNotEqual(self.rows['P9004']['lat'], '40.007200')

    def test_a_spot_near_its_town_gets_its_own_point(self):
        r = self.rows['P9005']
        self.assertEqual((r['match'], r['provider'], r['distance_km']), ('own-point', 'nominatim', '1.2'))
        self.assertEqual(self.ops['P9005']['set'], {'lat': 40.0108, 'long': -3.0})

    def test_an_override_wins(self):
        r = self.rows['P9006']
        self.assertEqual((r['match'], r['lat'], r['long']), ('override', '40.005000', '-3.005000'))
        self.assertEqual(self.ops['P9006']['set'], {'lat': 40.005, 'long': -3.005})
        self.assertIn('1900 town plan', r['note'])

    def test_a_street_has_the_tighter_limit(self):
        r = self.rows['P9011']
        self.assertEqual(r['match'], 'town-centroid')
        self.assertIn('over the 2 km limit', r['note'])

    def test_a_hamlet_nobody_has_takes_its_town(self):
        self.assertEqual((self.rows['P9007']['match'], self.rows['P9007']['lat']), ('town-centroid', '40.000000'))

    def test_a_spot_with_no_town_above_gets_no_point(self):
        # the dump has a Torre Lontana, but with no town there is nothing to check the 5 km against
        r = self.rows['P9014']
        self.assertEqual(r['match'], 'unresolved')
        self.assertIn('no town above it', r['note'])
        self.assertNotIn('P9014', self.ops)

    def test_a_town_of_the_same_name_is_not_the_spot(self):
        # «Santa Prueba» the hamlet is nearer, but a town is never the point of a chapel
        self.assertEqual(self.rows['P9005']['lat'], '40.010800')

    def test_a_spot_under_a_town_no_event_uses(self):
        r = self.rows['P9009']
        self.assertEqual((r['match'], r['lat']), ('own-point', '40.201000'))


class Containers(unittest.TestCase):
    def test_a_container_gets_no_point(self):
        code, rows, ops, http, _ = run()
        self.assertEqual(code, 0)
        # Otroejemplo was looked up, as the town its chapel is checked against, but no event uses it
        self.assertTrue(any(c[1].get('q') == 'Otroejemplo' for c in http.calls))
        self.assertNotIn('P9008', ops)
        self.assertNotIn('P9008', rows)
        self.assertNotIn('P9031', ops)

    def test_a_container_with_a_point_is_reported(self):
        _, rows, ops, _, _ = run()
        self.assertEqual(rows['P9002']['match'], 'container')
        self.assertNotIn('P9002', ops)

    def test_an_override_on_a_container_writes_nothing(self):
        _, rows, ops, _, _ = run(overrides='P9008\t40.3\t-3.3\n')
        self.assertNotIn('P9008', ops)
        # the chapel is now checked against the overridden town, 14 km from it, so it falls back to that point
        self.assertEqual((rows['P9009']['match'], rows['P9009']['lat']), ('town-centroid', '40.300000'))


class Settlements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.rows, cls.ops, cls.http, _ = run()

    def test_spain_through_cartociudad(self):
        r = self.rows['P9003']
        self.assertEqual((r['match'], r['provider'], r['lat']), ('settlement', 'cartociudad', '40.000000'))

    def test_a_town_and_its_municipality_are_one_place(self):
        # CartoCiudad gives the town's point and the municipality's centroid, 22 km apart, with one INE code:
        # that is not two namesakes, and the town's point wins
        self.assertEqual((self.rows['P9003']['match'], self.rows['P9003']['lat']), ('settlement', '40.000000'))

    def test_a_spanish_toponym(self):
        r = self.rows['P9015']
        self.assertEqual((r['match'], r['provider'], r['distance_km']), ('own-point', 'cartociudad', '0.2'))

    def test_an_unknown_country_says_how_to_name_it(self):
        r = self.rows['P9051']
        self.assertEqual(r['match'], 'unresolved')
        self.assertIn('--country Ruritania=CC', r['note'])

    def test_france_through_ban_by_exact_name(self):
        r = self.rows['P9022']
        self.assertEqual((r['provider'], r['lat'], r['long']), ('ban', '48.500000', '-1.500000'))

    def test_italy_through_the_geonames_dump(self):
        self.assertEqual((self.rows['P9032']['provider'], self.rows['P9032']['lat']), ('geonames-dump', '43.300000'))
        r = self.rows['P9033']
        self.assertEqual((r['match'], r['provider'], r['lat']), ('own-point', 'geonames-dump', '43.305000'))

    def test_a_country_an_event_uses_gets_its_centroid(self):
        self.assertEqual((self.rows['P9030']['match'], self.rows['P9030']['lat']), ('area', '42.833330'))

    def test_another_country_is_refused(self):
        r = self.rows['P9010']
        self.assertEqual(r['match'], 'unresolved')
        self.assertIn('another country (pt)', r['note'])
        self.assertNotIn('P9010', self.ops)

    def test_two_far_namesakes_and_nothing_to_choose_by(self):
        r = self.rows['P9034']
        self.assertEqual(r['match'], 'unresolved')
        self.assertIn('2 places of that name', r['note'])

    def test_ops_are_update_place_with_a_ruling(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'c.jsonl')
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                G.main(['--snapshot', TREE, '--out', out, '--no-cache', '--ruling', 'R-7'], http=FakeHttp())
            with open(out, encoding='utf-8') as f:
                op = json.loads(f.readline())
        self.assertEqual(set(op), {'op', 'key', 'ruling', 'place', 'set'})
        self.assertEqual((op['op'], op['ruling']), ('update_place', 'R-7'))
        self.assertEqual(op['key'], 'geocode-' + op['place'])


class Countries(unittest.TestCase):
    def test_a_country_code_given_by_hand(self):
        _, rows, _, http, _ = run('--country', 'Ruritania=RR')
        self.assertIn({'q': 'Pueblo Raro, Ruritania', 'countrycodes': 'rr', 'format': 'jsonv2', 'addressdetails': '1',
                       'namedetails': '1', 'limit': '10'}, [c[1] for c in http.calls])
        self.assertNotIn('--country', rows['P9051']['note'])

    def test_an_unknown_country_never_takes_a_namesake_abroad(self):
        # the dump has an Italian Pueblo Raro; with no code for Ruritania nothing may be searched worldwide
        _, rows, ops, http, _ = run('--providers', 'geonames-dump,nominatim')
        self.assertEqual(rows['P9051']['match'], 'unresolved')
        self.assertIn('--country Ruritania=CC', rows['P9051']['note'])
        self.assertNotIn('P9051', ops)
        self.assertFalse(any(c[1].get('q') == 'Pueblo Raro, Ruritania' for c in http.calls))


class KeepPoints(unittest.TestCase):
    def test_places_with_a_point_are_left_alone_by_default(self):
        for extra in ((), ('--only-missing',)):
            _, rows, ops, http, _ = run(*extra)
            self.assertEqual(rows['P9013']['match'], 'kept')
            self.assertNotIn('P9013', ops)
            self.assertNotIn('P9016', ops)
            self.assertFalse(any(c[1].get('q') == 'Villa Movida' for c in http.calls))

    def test_recheck_moves_a_point_and_says_from_where(self):
        _, rows, ops, _, _ = run('--recheck')
        self.assertEqual(ops['P9013']['set'], {'lat': 40.61, 'long': -3.61})
        self.assertIn("moves the tree's point 1.4 km, from 40.600000, -3.600000", rows['P9013']['note'])

    def test_recheck_ignores_a_move_of_a_few_metres(self):
        _, rows, ops, _, _ = run('--recheck')
        self.assertEqual(rows['P9012']['match'], 'settlement')
        self.assertNotIn('P9012', ops)

    def test_recheck_never_puts_the_town_over_a_spot_of_the_tree(self):
        # the old convent is in no gazetteer: its hand-placed point must not become the town's
        _, rows, ops, _, _ = run('--recheck')
        self.assertEqual(rows['P9016']['match'], 'town-centroid')
        self.assertNotIn('P9016', ops)
        self.assertEqual((rows['P9016']['lat'], rows['P9016']['long']), ('40.004000', '-3.004000'))
        self.assertIn("keeps the tree's point", rows['P9016']['note'])

    def test_a_point_in_degrees_and_minutes_is_kept_as_it_is(self):
        # Gramps also stores 40°10'00"N; the tool cannot read it, so it must never overwrite it
        for extra in ((), ('--recheck',)):
            _, rows, ops, _, _ = run(*extra)
            self.assertEqual(rows['P9017']['match'], 'kept')
            self.assertIn('not in decimal degrees', rows['P9017']['note'])
            self.assertNotIn('P9017', ops)

    def test_a_container_with_a_point_in_degrees_is_reported(self):
        _, rows, _, _, _ = run()
        self.assertEqual(rows['P9019']['match'], 'container')

    def test_an_override_wins_over_a_kept_point(self):
        _, rows, ops, _, _ = run(overrides='P9013\t40.7\t-3.7\n')
        self.assertEqual(ops['P9013']['set'], {'lat': 40.7, 'long': -3.7})


class Names(unittest.TestCase):
    def test_same_ending(self):
        self.assertTrue(G.same_ending('Iglesia de San Ejemplo', 'San Ejemplo', 'es'))
        self.assertTrue(G.same_ending('Iglesia de San Ejemplo', 'Parroquia de San Ejemplo', 'es'))
        self.assertFalse(G.same_ending('Iglesia de San Ejemplo', 'Parroquia de San Ejemplo Obrero', 'es'))
        self.assertFalse(G.same_ending('Iglesia de San Ejemplo', 'San Ejemplo Obrero', 'es'))
        self.assertFalse(G.same_ending('San Ejemplo', 'Parroquia de San Ejemplo Obrero', 'es'))
        self.assertFalse(G.same_ending('San Ejemplo', 'Santa María', 'es'))
        self.assertTrue(G.same_ending('Parroquia de N.ª S.ª del Ejemplo', 'Nuestra Señora del Ejemplo', 'es'))
        self.assertFalse(G.same_ending('Iglesia de Villa Prueba', 'Villa Prueba', 'es', 'Villa Prueba'))
        self.assertTrue(G.same_ending('Iglesia de Villa Prueba', 'Villa Prueba', 'es', 'Otra Villa'))

    def test_same_name(self):
        self.assertTrue(G.same_name('Villanueva (Vilanova)', ['Vilanova']))
        self.assertTrue(G.same_name('Saint-Exemple', ['saint exemple']))
        self.assertFalse(G.same_name('Saint-Exemple', ['Saint-Exemple-sur-Mer']))


class Transport(unittest.TestCase):
    def test_nominatim_gets_one_request_a_second(self):
        now = [0.0]
        slept = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def sleep(s):
            slept.append(s)
            now[0] += s
        h = G.Http(None, clock=lambda: now[0], sleep=sleep, opener=lambda req, timeout: Resp(b'[]'))
        h.get_json(G.Nominatim.URL, {'q': 'a'})
        h.get_json(G.Nominatim.URL, {'q': 'b'})
        self.assertEqual(len(slept), 1)
        self.assertGreaterEqual(slept[0], 1.0)

    def test_the_cache_answers_a_repeat(self):
        seen = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            seen.append(req.get_header('User-agent'))
            return Resp(b'[{"x": 1}]')
        with tempfile.TemporaryDirectory() as d:
            h = G.Http(d, contact='someone@example.org', clock=lambda: 1e6, sleep=lambda s: None, opener=opener)
            self.assertEqual(h.get_json(G.Nominatim.URL, {'q': 'a'}), [{'x': 1}])
            self.assertEqual(h.get_json(G.Nominatim.URL, {'q': 'a'}), [{'x': 1}])
        self.assertEqual(len(seen), 1)
        self.assertIn('someone@example.org', seen[0])
        self.assertIn('github.com/GeiserX/gramps-web-toolkit', seen[0])


class BadInputs(unittest.TestCase):
    def test_not_a_snapshot(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            f.write('[1, 2]')
        try:
            code, *_, err = run_with(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(code, 2)

    def test_a_bad_override_row(self):
        code, *_ = run(overrides='P9006\tforty\t-3\n')
        self.assertEqual(code, 2)

    def test_an_unknown_provider(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            G.main(['--snapshot', TREE, '--out', os.devnull, '--providers', 'nominatim,nope'], http=FakeHttp())


def run_with(snapshot):
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = G.main(['--snapshot', snapshot, '--out', os.devnull, '--no-cache'], http=FakeHttp())
    return code, err.getvalue()


if __name__ == '__main__':
    unittest.main()
