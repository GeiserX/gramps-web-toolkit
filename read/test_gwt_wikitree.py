"""Tests for `gwt-read wikitree`. Answers in fixtures/wikitree/ keep the API's shape with invented profiles; a fake
transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q test_gwt_wikitree.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402
import gwt_wikitree  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'wikitree')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def run_wt(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['wikitree', '--cache-dir', self.cache, '--lane', 't'] + args + ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_wt(['search', '--surname', 'Zorvalo', '--given', 'Ana', *extra], net, gate)


class Classify(unittest.TestCase):
    def test_hits_none_and_a_changed_container_is_unknown(self):
        v = gwt_wikitree.verdict
        self.assertEqual(v(json.loads(fx('search.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'a renamed result container read as "none"')
        self.assertEqual(v([{'status': 'Permission denied', 'matches': [], 'total': 0}]), 'unknown')
        self.assertEqual(v({'status': 0, 'matches': []}), 'unknown')
        self.assertEqual(v([{'status': 0, 'matches': [], 'total': 5}]), 'unknown', 'a count with no rows read as "none"')
        self.assertEqual(v([{'status': 0, 'matches': [{'index': 0}], 'total': 1}]), 'unknown', 'a row with no Id')
        self.assertEqual(v([{'status': 0, 'matches': [{'Id': 1, 'Name': 'X-1'}], 'total': '1'}]), 'unknown',
                         'a count that is not a whole number')

    def test_a_private_profile_is_only_its_id(self):
        # the live API gives a private profile as {Id, index} alone: that is a withheld row, not a changed answer
        doc = [{'status': 0, 'matches': [{'Id': 900009, 'index': 0}], 'total': 1}]
        self.assertEqual(gwt_wikitree.verdict(doc), 'hits')
        self.assertEqual(gwt_wikitree.candidates(doc[0]['matches']), ([], 1))

    def test_records(self):
        v = gwt_wikitree.record_verdict
        self.assertEqual(v(json.loads(fx('profile.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('people.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('invalid.json'))), 'none', 'the site\'s own "Invalid WikiTree ID"')
        self.assertEqual(v([{'page_name': 'X-1', 'status': 'Something new'}]), 'unknown')
        self.assertEqual(v([{'status': '', 'people': {}}]), 'none', 'getPeople with no person')
        self.assertEqual(v([{'status': '', 'people': 'Zorvalo-12'}]), 'unknown', 'people of another type')
        self.assertEqual(v([{'status': 'Permission denied', 'profile': {'Id': 1}}]), 'unknown',
                         'getProfile with an error status')

    def test_a_public_profile_without_its_wikitree_id_is_withheld_never_a_crash(self):
        self.assertEqual(gwt_wikitree.candidates([{'Id': 1, 'Privacy_IsAtLeastPublic': True}]), ([], 1))

    def test_the_asked_profile_goes_first_whatever_the_answers_order_and_generations_count_up(self):
        people = [{'Id': 3, 'Name': 'Zorvalo-3', 'Privacy_IsAtLeastPublic': True},
                  {'Id': 2, 'Name': 'Zorvalo-2', 'Privacy_IsAtLeastPublic': True, 'Father': 3},
                  {'Id': 1, 'Name': 'Zorvalo-1', 'Privacy_IsAtLeastPublic': True, 'Father': 2}]
        cands, _ = gwt_wikitree.candidates(people)
        rows = gwt_wikitree.mark_ancestors(cands, people, 1)
        self.assertEqual([(c['id'], c['principal'], c.get('generation')) for c in rows],
                         [('Zorvalo-1', True, None), ('Zorvalo-3', False, 2), ('Zorvalo-2', False, 1)])

    def test_profile_keys(self):
        self.assertEqual(gwt_wikitree.profile_key('Zorvalo-12'), 'Zorvalo-12')
        self.assertEqual(gwt_wikitree.profile_key('https://www.wikitree.com/wiki/Zorvalo-12'), 'Zorvalo-12')
        # a URL copied from a browser comes percent-encoded; sent as is, it is encoded twice and reads as no profile
        self.assertEqual(gwt_wikitree.profile_key('https://www.wikitree.com/wiki/M%C3%BCller-12'), 'Müller-12')
        self.assertIsNone(gwt_wikitree.profile_key('Zorvalo'))

    def test_an_unknown_date_is_no_year(self):
        p = {'Name': 'Zorvalo-8', 'BirthDate': '0000-00-00', 'BirthLocation': 'Nortia'}
        self.assertEqual(gwt_wikitree.candidate(p)['events'], {'birth': {'year': None, 'year_to': None, 'place': 'Nortia'}})

    def test_a_year_keeps_only_profiles_that_have_the_dates(self):
        self.assertIn('dateInclude=both', gwt_wikitree.search_url(surname='Zorvalo', born=1851))
        self.assertNotIn('dateInclude', gwt_wikitree.search_url(surname='Zorvalo'))

    def test_ancestors_are_capped_at_10_generations(self):
        self.assertIn('ancestors=10', gwt_wikitree.profile_url('Zorvalo-12', 50))


class Search(Run):
    def test_public_profiles_are_candidates_private_ones_only_counted(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, fx('search.json'))), gate)
        self.assertEqual(code, 0, text)
        cands = json.load(open(lines['candidates']))['candidates']
        self.assertEqual([c['id'] for c in cands], ['Zorvalo-12', 'Zorvalo-7'], 'a private profile became a candidate')
        a = cands[0]
        self.assertEqual((a['name'], a['given'], a['surnames']), ('Ana Rosa Zorvalo', 'Ana Rosa', ['Zorvalo', 'Quirell']))
        self.assertEqual(a['events'], {'birth': {'year': 1851, 'year_to': 1851, 'place': 'Villazul, Nortia'},
                                       'death': {'year': 1920, 'year_to': 1920, 'place': ''}})
        self.assertEqual(a['url'], 'https://www.wikitree.com/wiki/Zorvalo-12')
        self.assertEqual(cands[1]['events'], {}, 'an unknown date 0000-00-00 is no year')
        self.assertEqual(json.load(open(lines['evidence']))['withheld'], 1)
        self.assertIn('1 private withheld', lines['results'])

    def test_every_call_names_the_app(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')), (200, fx('profile.json')), (200, fx('people.json')))
        self.search(net, gate)
        self.run_wt(['record', 'Zorvalo-12'], net, gate)
        self.run_wt(['record', 'Zorvalo-12', '--ancestors', '2'], net, gate)
        self.assertEqual(len(net.requests), 3)
        for r in net.requests:
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.full_url).query))
            self.assertEqual(q.get('appId'), 'gramps-web-toolkit', r.full_url)

    def test_the_apis_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))

    def test_a_changed_answer_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('changed.json')), (200, fx('changed.json')))
        code, lines, text = self.search(net, gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a changed answer was served from the cache')

    def test_a_503_is_a_block(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (503, b'busy')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_a_search_without_a_name_asks_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _ = self.run_wt(['search', '--born', '1851'], net, gate)
        self.assertEqual((code, len(net.requests)), (64, 0))


class Record(Run):
    def test_one_profile(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('profile.json')))
        code, lines, text = self.run_wt(['record', 'https://www.wikitree.com/wiki/Zorvalo-12'], net, gate)
        self.assertEqual(code, 0, text)
        self.assertIn('action=getProfile', net.requests[0].full_url)
        (c,) = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((c['id'], c['principal']), ('Zorvalo-12', True))

    def test_ancestors_with_their_relatives_and_private_ones_withheld(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('people.json')))
        code, lines, text = self.run_wt(['record', 'Zorvalo-12', '--ancestors', '1'], net, gate)
        self.assertEqual(code, 0, text)
        self.assertIn('action=getPeople', net.requests[0].full_url)
        c = {x['id']: x for x in json.load(open(lines['candidates']))['candidates']}
        self.assertEqual(sorted(c), ['Zorvalo-12', 'Zorvalo-5'])
        self.assertEqual(c['Zorvalo-12']['relatives'], [{'role': 'father', 'name': 'Pedro Zorvalo'}],
                         'a private mother is named nowhere')
        self.assertEqual(json.load(open(lines['evidence']))['withheld'], 1)

    def test_the_asked_profile_is_the_principal_and_its_ancestors_are_not(self):
        gate = FakeGate()
        code, lines, text = self.run_wt(['record', 'Zorvalo-12', '--ancestors', '1'],
                                        FakeNet(gate, (200, fx('people.json'))), gate)
        self.assertEqual(code, 0, text)
        subject, father = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((subject['id'], subject['role'], subject['principal']), ('Zorvalo-12', 'profile', True))
        self.assertEqual((father['id'], father['role'], father['principal'], father['generation']),
                         ('Zorvalo-5', 'ancestor', False, 1))
        self.assertEqual(gwt_wikitree.mark_ancestors([{'id': 'X-1', 'principal': True}], [], None),
                         [{'id': 'X-1', 'principal': True}], 'an answer without resultByKey')

    def test_an_invalid_id_is_the_sites_own_none(self):
        gate = FakeGate()
        code, lines, text = self.run_wt(['record', 'Zorvalo-99999'], FakeNet(gate, (200, fx('invalid.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertEqual(json.load(open(lines['candidates'])), {'candidates': []})


if __name__ == '__main__':
    unittest.main()
