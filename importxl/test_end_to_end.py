"""Workbook to change list to gwt-apply: preflight and Applier on apply/tests/fake_api.py, with real writes to the fake."""
import gzip, json, os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..', 'apply'), os.path.join(HERE, '..', 'apply', 'tests')]
import gwt_import_excel as X
from test_gwt_import_excel import HEAD, MAP, make_xlsx, row

try:
    from fake_api import FakeGrampsWeb
    from gwt_apply.client import Client
    from gwt_apply.dupes import preflight
    from gwt_apply.ops import Applier, validate
except ImportError as exc:   # gwt-apply needs requests
    FakeGrampsWeb = None
    WHY = str(exc)


def person(fake, first, sur):
    return fake.add('people', {'gender': 1, 'primary_name': {'first_name': first, 'surname_list': [{'surname': sur, 'primary': True}]}})


@unittest.skipIf(FakeGrampsWeb is None and not os.environ.get('CI'), 'gwt-apply not importable')   # CI must run it
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGrampsWeb()
        self.fake.add('places', {'handle': 'plc1', 'name': {'value': 'Springfield'}, 'place_type': 'City'})   # P0001
        person(self.fake, 'Anna', 'Example')                                                                  # I0001, born 1800
        self.client = Client('https://gw.example', 'u', 'pw', dry_run=False, session=self.fake)
        self.client.login()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def dump(self):
        p = os.path.join(self.dir.name, 'dump-%d.json.gz' % len(os.listdir(self.dir.name)))
        objs = {ns: list(self.fake.store[ns].values()) for ns in ('people', 'places')}
        with gzip.open(p, 'wt', encoding='utf-8') as f:
            json.dump({'objects': objs}, f)
        return X.read_tree(p)

    def convert(self, rows, batch, sheet='People'):
        p = os.path.join(self.dir.name, '%s-%d.xlsx' % (batch, len(os.listdir(self.dir.name))))
        make_xlsx(p, {sheet: [HEAD] + rows})
        mapping = json.loads(json.dumps(MAP))
        mapping['sheets'][0]['sheet'] = sheet
        ops, rep = X.import_rows(X.read_workbook(p), mapping, self.dump(), batch=batch, this_year=2026)
        self.assertEqual(validate(ops), [])
        return ops, rep

    def apply(self, ops):
        self.assertEqual(preflight(ops, self.client), [])
        results = Applier(self.client, retries=1, retry_wait=0).run(ops)
        self.assertEqual([(r.key, r.status) for r in results if r.status not in ('done', 'skip')], [])
        return results

    def people(self, first, sur):
        return [p for p in self.fake.store['people'].values()
                if p['primary_name']['first_name'] == first and p['primary_name']['surname_list'][0]['surname'] == sur]

    def pages(self, p):
        cits = {c['handle']: c for c in self.fake.store['citations'].values()}
        return sorted(cits[h]['page'] for h in p['citation_list'])

    def test_two_workbooks_namesakes_and_places_through_preflight_and_applier(self):
        book_a = [row('Luis', 'Gil', 'M', '1899', 'Springfield', src='Book', page='f 1'),
                  row('Anna', 'Example', 'F', '1950', died='2010', src='Book', page='f 2')]
        ops, rep = self.convert(book_a, 'cousin-a')
        # Anna Example has a namesake in the tree and no reason: preflight refuses, nothing is written
        problems = preflight(ops, self.client)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn('I0001', problems[0])
        self.assertEqual(rep['namesakes'][0]['tree_ids'], ['I0001'])
        # the reviewer says why she is another person, and the list goes through
        book_a[1][9] = 'born 1950; I0001 was born 1800'
        ops, rep = self.convert(book_a, 'cousin-a')
        self.apply(ops)
        self.assertEqual(len(self.fake.store['places']), 1)                          # Springfield reused, no new place
        luis_a = self.people('Luis', 'Gil')[0]
        births = [e for e in self.fake.store['events'].values() if e['type'] == 'Birth']
        self.assertEqual({e['place'] for e in births if e.get('place')}, {'plc1'})
        self.assertEqual(len(self.people('Anna', 'Example')), 2)
        # a rerun of the same list writes nothing
        before = len(self.fake.writes())
        self.assertEqual({r.status for r in self.apply(ops)}, {'skip'})
        self.assertEqual(len(self.fake.writes()), before)

        # a second workbook has another Luis Gil born 1899: never merged into the first
        book_b = [row('Luis', 'Gil', 'M', '1899', 'Otherton', '1960', src='Other book', page='f 9')]
        ops_b, rep_b = self.convert(book_b, 'archive-b')
        self.assertFalse({o['key'] for o in ops_b} & {o['key'] for o in ops})
        problems = preflight(ops_b, self.client)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(luis_a['gramps_id'], problems[0])
        book_b[0][9] = 'buried in Otherton 1960; the cousin-a Luis stayed in Springfield'
        ops_b, rep_b = self.convert(book_b, 'archive-b')
        self.assertEqual(rep_b['places_unmatched'][0]['text'], 'Otherton')
        self.apply(ops_b)
        luis = self.people('Luis', 'Gil')
        self.assertEqual(len(luis), 2)
        luis_a = self.fake.store['people'][luis_a['handle']]
        luis_b = next(p for p in luis if p['handle'] != luis_a['handle'])
        self.assertEqual(self.pages(luis_a), ['f 1'])
        self.assertEqual(self.pages(luis_b), ['f 9'])
        self.assertEqual(len(self.fake.store['places']), 1)
        self.assertEqual(len(luis_a['event_ref_list']), 1)
        self.assertEqual(len(luis_b['event_ref_list']), 2)


    def test_two_sheets_whose_names_start_alike_never_share_a_person(self):
        # two runs under one batch, one sheet each; the names cut to 20 characters are the same
        first = [row('José', 'García', 'M', '1850', src='Bautismos 1850', page='f 1')]
        ops1, _ = self.convert(first, 'parish', sheet='Bautismos Santiago 1850')
        self.apply(ops1)
        jose_1 = self.people('José', 'García')[0]
        second = [row('José', 'García', 'M', '1851', src='Bautismos 1851', page='f 7')]
        ops2, _ = self.convert(second, 'parish', sheet='Bautismos Santiago 1851')
        self.assertFalse({o['key'] for o in ops1 if o['op'] != 'add_source'} & {o['key'] for o in ops2})
        problems = preflight(ops2, self.client)
        self.assertEqual(len(problems), 1, problems)                 # the 1850 José is a namesake, not the same key
        self.assertIn(jose_1['gramps_id'], problems[0])
        second[0][9] = 'baptised 1851, a year after the other José'
        ops2, _ = self.convert(second, 'parish', sheet='Bautismos Santiago 1851')
        self.apply(ops2)
        jose = self.people('José', 'García')
        self.assertEqual(len(jose), 2)
        self.assertEqual(sorted(self.pages(p) for p in jose), [['f 1'], ['f 7']])


if __name__ == '__main__':
    unittest.main()
