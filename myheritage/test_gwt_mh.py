"""Tests for gwt-mh's pure logic. Synthetic fixtures only (invented people and ids; public collection ids),
no network, no browser. Run: cd myheritage && python3 -m unittest -v test_gwt_mh"""
import copy, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
for sub in ('ledger', 'fscache', 'rank'):
    sys.path.insert(0, os.path.join(HERE, '..', sub))
sys.path.insert(0, HERE)

import gwt_mh as mh  # noqa: E402
import gwt_ledger  # noqa: E402
from gwt_fscache import FSCache  # noqa: E402

try:
    import gwt_rank
except ImportError:      # the ranker lives in ../rank; skip its checks where it is absent
    gwt_rank = None


def load(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return f.read() if not name.endswith('.json') else json.load(f)


def slurp(path, mode='r'):
    with open(path, mode) as f:
        return f.read()


def env_of(name):
    return {'graphql': load(name)}


class Normalizer(unittest.TestCase):
    def test_basic_masks_become_null_and_are_listed(self):
        cands, total = mh.candidates_from_envelope(env_of('graphql_basic.json'))
        self.assertEqual(total, 2)
        self.assertEqual(len(cands), 2, 'the split answer repeats a record; it is read once')
        tree = cands[0]
        self.assertEqual(tree['id'], '40001:9000001')
        self.assertEqual(tree['events']['birth'], {'year': 1851, 'year_to': 1851, 'place': ''})
        self.assertEqual(tree['mh']['masked'], ['birth.day', 'birth.month', 'birth.place', 'children',
                                                'death.day', 'death.month', 'death.place', 'parents', 'spouse'])
        dump = json.dumps(cands, ensure_ascii=False)
        for word in ('Día', 'Mes', 'Lugar', 'nonFreeComponentLink', 'nombre de 3'):
            self.assertNotIn(word, dump)
        self.assertEqual(tree['relatives'], [{'role': 'child', 'name': 'Luis Quirell Zorvalo'}])
        self.assertTrue(tree['principal'])
        free = cands[1]
        self.assertEqual(free['events']['birth']['place'], 'Villazul, España')
        self.assertEqual(free['mh']['masked'], [])

    def test_paid_fields_unmasked(self):
        c = mh.candidates_from_envelope(env_of('graphql_paid.json'))[0][0]
        self.assertEqual(c['events']['birth']['place'], 'Villazul, Murcia, España')
        self.assertEqual(c['mh']['masked'], [])
        self.assertIn({'role': 'parent', 'name': 'María Brineda Sol'}, c['relatives'])
        self.assertIn({'role': 'spouse', 'name': 'Tomás Quirell'}, c['relatives'])

    def test_oldnews_fields_and_hint(self):
        c = mh.candidates_from_envelope(env_of('graphql_oldnews.json'))[0][0]
        self.assertEqual(c['source'], 'myheritage-oldnews')
        self.assertEqual(c['id'], '12040:800001-')
        self.assertEqual(c['mh']['paper'], 'El Eco Inventado')
        self.assertEqual(c['mh']['pub_date'], '4 de junio de 1892')
        self.assertEqual(c['mh']['pub_place'], 'Villazul, Murcia, Spain')
        self.assertIn('Tomás Quirell calle Mayor', c['mh']['ocr_snippet'])
        self.assertNotIn('<span', c['mh']['ocr_snippet'])
        self.assertEqual(c['mh']['image_hint'], {'title': 'el_eco_inventado_1880', 'date': '1892-06-04',
                                                 'issue': '1000001', 'page': 3})
        self.assertNotIn('perm_id', c['mh']['link'])
        self.assertNotIn('recordTitle', c['mh']['link'])

    @unittest.skipIf(gwt_rank is None, 'ranker not present')
    def test_ranker_accepts_every_candidate(self):
        for name in ('graphql_basic.json', 'graphql_paid.json', 'graphql_oldnews.json'):
            cands = mh.candidates_from_envelope(env_of(name))[0]
            for c in cands:
                self.assertEqual(gwt_rank._bad_record(c), '', (name, c['id']))
            rows = gwt_rank.normalized_rows({'candidates': cands})
            self.assertTrue(rows and all(r['principal'] for r in rows))

    @unittest.skipIf(gwt_rank is None, 'ranker not present')
    def test_paid_record_ranks_strong(self):
        cands = mh.candidates_from_envelope(env_of('graphql_paid.json'))[0]
        res = gwt_rank.rank(gwt_rank.normalized_rows({'candidates': cands}), 'Ana', ['Zorvalo'],
                            gwt_rank.parse_facts('by=1851 bp=Villazul'), 'Tomas Quirell')
        self.assertEqual(res[0]['tier'], 'strong')

    def test_cli_normalize_offline(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, 'gwt-mh'), 'normalize',
                            os.path.join(FIX, 'graphql_paid.json')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['candidates'][0]['id'], '40001:9000002')


class Plan(unittest.TestCase):
    def obs(self, name):
        items, _ = mh.graphql_records(env_of(name))
        return mh.search_observations(items, 'u')

    def test_basic_paid_omni_unknown(self):
        self.assertEqual(mh.plan_label(mh.capabilities(self.obs('graphql_basic.json'))), 'basic')
        self.assertEqual(mh.plan_label(mh.capabilities(self.obs('graphql_paid.json'))), 'data+')
        self.assertEqual(self.obs('graphql_oldnews.json'), [], 'OldNews text alone proves nothing')
        self.assertEqual(mh.plan_label(mh.capabilities([])), 'unknown')
        parsed = mh.parse_record_text(mh.html_to_text(load('record_en.html')))
        omni = self.obs('graphql_paid.json') + mh.record_observations(mh.HOST + '/research/collection-12040/x?itemId=1&action=showRecord',
                                                                    collection_free=False, parsed=parsed)
        self.assertEqual(mh.plan_label(mh.capabilities(omni)), 'omni')

    def test_any_mask_on_a_page_means_basic(self):
        items = mh.graphql_records(env_of('graphql_basic.json'))[0] + mh.graphql_records(env_of('graphql_paid.json'))[0]
        self.assertEqual(mh.search_observations(items), [{'signal': 'records', 'value': False, 'url': ''}])

    def test_newest_observation_wins(self):
        o = [{'at': '2026-01-01T00:00:00Z', 'signal': 'records', 'value': True},
             {'at': '2026-03-01T00:00:00Z', 'signal': 'records', 'value': False}]
        self.assertEqual(mh.plan_label(mh.capabilities(o)), 'basic')
        self.assertEqual(mh.plan_label(mh.capabilities(list(reversed(o)))), 'basic')
        o.append({'at': '2026-04-01T00:00:00Z', 'signal': 'records', 'value': True})
        self.assertEqual(mh.plan_label(mh.capabilities(o)), 'data+')

    def test_paywall_record_is_basic_evidence(self):
        url = load('paywall_final_url.txt').strip()
        self.assertEqual(mh.record_observations(url), [{'signal': 'records', 'value': False, 'url': ''}])
        smart = mh.HOST + '/paywall?reason=178&x=1'
        self.assertEqual(mh.record_observations(smart)[0]['signal'], 'tree_review')

    def test_plan_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            ps = mh.PlanState(d)
            self.assertEqual(ps.label(), 'unknown')
            ps.add(self.obs('graphql_basic.json'))
            self.assertEqual(json.loads(slurp(os.path.join(d, 'plan.json')))['plan'], 'basic')

    def test_positive_control_one_unmasked_field_breaks_basic(self):
        """The basic expectation must be able to fail: unmask one record and it is no longer basic."""
        doc = copy.deepcopy(load('graphql_basic.json'))
        items, _ = mh.graphql_records({'graphql': doc})
        self.assertEqual(mh.plan_label(mh.capabilities(mh.search_observations(items))), 'basic')
        for f in items[0]['record']['display_fields']:
            if mh.is_masked_value(f['value']):
                f['value'] = '3 de mayo de 1851 - Villazul' if f['name'] in ('birth', 'death') else 'Pedro Zorvalo'
        items, _ = mh.graphql_records({'graphql': doc})
        self.assertNotEqual(mh.plan_label(mh.capabilities(mh.search_observations(items))), 'basic')


class Walls(unittest.TestCase):
    def test_imperva_login_paywall(self):
        self.assertEqual(mh.classify(mh.HOST + '/research', '', load('imperva.html')), 'wall')
        self.assertEqual(mh.EXIT_FOR['wall'], 3)
        self.assertEqual(mh.classify(mh.HOST + '/research', mh.html_to_text(load('login_wall.html'))), 'dead')
        self.assertEqual(mh.classify(mh.HOST + '/login?x=1', 'x'), 'dead')
        self.assertEqual(mh.EXIT_FOR['dead'], 4)
        self.assertEqual(mh.classify(load('paywall_final_url.txt').strip(), 'Opciones de cuenta'), 'paywall')
        self.assertEqual(mh.EXIT_FOR['paywall'], 5)
        self.assertEqual(mh.classify(mh.HOST + '/research?x', 'Opciones de cuenta\nIniciar sesión'), 'ok')


class CacheKey(unittest.TestCase):
    def test_ignores_session_params_and_order(self):
        a = mh.normalize_url(mh.HOST + '/research/collection-1/x?itemId=A-1&action=showRecord&recordTitle=Ana&perm_id=T_1&csrf_token=z')
        b = mh.normalize_url('https://WWW.myheritage.es/research/collection-1/x?action=showRecord&itemId=A-1&utm_source=q')
        self.assertEqual(a, b)
        self.assertNotEqual(a, mh.normalize_url(mh.HOST + '/research/collection-1/x?itemId=A-2&action=showRecord'))

    def test_plan_is_part_of_the_key_and_tokens_never_stored(self):
        with tempfile.TemporaryDirectory() as d:
            cache = FSCache(d, rules=mh.MYHERITAGE_RULES, vary=(mh.PLAN_HEADER,))
            url = mh.normalize_url(mh.HOST + '/research?action=query&qname=Name')
            self.assertNotEqual(cache.key('GET', url, {mh.PLAN_HEADER: 'basic'}),
                                cache.key('GET', url, {mh.PLAN_HEADER: 'data+'}))
            self.assertEqual(cache.ttl(url), 7 * mh.DAY)
            self.assertIsNone(cache.ttl(mh.HOST + '/research/collection-1/x?itemId=1&action=showRecord'))
            env = {'url': url, 'text': 'x', 'html': load('record_es.html'),
                   'graphql': [{'url': 'g', 'post': '--b\r\nContent-Disposition: form-data; name="bearer_token"\r\n\r\nSECRETVALUE123\r\n--b',
                                'body': {'bearer_token': 'SECRETVALUE456', 'headers': {'Authorization': 'Bearer ' + 'q' * 24}}}]}
            for planted in ('tok_INVENTED', 'XSRF_INVENTED', 'FG_INVENTED', 'FGW_INVENTED', 'CSRF_INVENTED'):
                self.assertIn(planted, json.dumps(env))
            stored = json.dumps(mh.scrub(env))
            cache.store('GET', url, {mh.PLAN_HEADER: 'basic'}, None, 200, {}, stored.encode())
            blob = b''.join(slurp(os.path.join(r, f), 'rb') for r, _, fs in os.walk(d) for f in fs)
            for secret in (b'tok_INVENTED', b'XSRF_INVENTED', b'FG_INVENTED', b'FGW_INVENTED', b'CSRF_INVENTED',
                           b'SECRETVALUE123', b'SECRETVALUE456', b'q' * 24):
                self.assertNotIn(secret, blob)

    def test_wall_is_never_cached(self):
        with tempfile.TemporaryDirectory() as d:
            cache = FSCache(d, rules=mh.MYHERITAGE_RULES, vary=(mh.PLAN_HEADER,))
            self.assertFalse(cache.store('GET', 'u', {}, None, 403, {}, b'wall'))


class Paths(unittest.TestCase):
    def test_thumbnail_and_original_paths(self):
        t = 'https://cf.myheritageimages.com/records/thumb/resize/x/boletin_inventado/1957/05/15/numero_129/0028_96x_ab12.jpg'
        self.assertEqual(mh.image_hint(t), {'title': 'boletin_inventado', 'date': '1957-05-15', 'issue': 'numero_129', 'page': 28})
        o = 'https://sites-cf.example/t/sig/filters:blur(12)/original_records/x/gaceta_inventada/1890/01/02/n2/0004.jpg'
        self.assertEqual(mh.image_hint(o)['page'], 4)
        self.assertIsNone(mh.image_hint('https://cf.example/collections/assets/a.png'))

    def test_search_url_shape(self):
        u = mh.search_url('José Antonio', ['Zorvalo', 'Brineda'], by=1851, dy=1910, spouse='Tomás Quirell',
                          father='Pedro Zorvalo', mother='María Brineda', collection='40001', exact=True)
        self.assertIn('/research/collection-40001/x?', u)
        self.assertIn('qname=Name+fn.Jos%C3%A9+Antonio+ln.Zorvalo/3Brineda', u)
        self.assertIn('qevents-event1=Event+et.birth+ey.1851', u)
        self.assertIn('qevents-any/1event_2=Event+et.death+ey.1910', u)
        self.assertIn('exactSearch=1', u)
        self.assertIn('qrelatives-relative=Relative+rt.father', u)
        self.assertIn('qrelatives-addRelative_1=Relative+rt.mother', u)
        self.assertIn('qspouse=Name+fn.Tom%C3%A1s+ln.Quirell', u)


class Manifest(unittest.TestCase):
    def test_rows(self):
        with tempfile.TemporaryDirectory() as d:
            rows = []
            for name, kind in (('tiny.jpg', 'image'), ('tiny.png', 'thumbnail'), ('tiny.jpg', 'screenshot-fallback')):
                p = os.path.join(d, kind + os.path.splitext(name)[1])
                with open(os.path.join(FIX, name), 'rb') as src, open(p, 'wb') as dst:
                    dst.write(src.read())
                rows.append(mh.manifest_row(p, kind, 'https://x/' + name, 'data+', '12040', '1-'))
            img, th, sc = rows
            self.assertEqual((img['width'], img['height'], img['original']), (16, 9, 'yes'))
            self.assertEqual((th['width'], th['height'], th['original']), (16, 9, 'no'))
            self.assertEqual(sc['original'], 'no')
            self.assertEqual(len(img['sha256']), 64)
            self.assertEqual(img['size'], os.path.getsize(os.path.join(FIX, 'tiny.jpg')))
            m = os.path.join(d, 'manifest.tsv')
            mh.write_manifest(m, rows)
            mh.write_manifest(m, [dict(img, plan='omni')])
            back = mh.read_manifest(m)
            self.assertEqual(len(back), 3)
            self.assertEqual([r['plan'] for r in back if r['kind'] == 'image'], ['omni'])
            self.assertEqual(slurp(m).split('\n')[0].split('\t'), list(mh.MANIFEST_COLUMNS))


class FetchPlan(unittest.TestCase):
    def test_basic_plans_no_image(self):
        caps = mh.capabilities([{'signal': 'records', 'value': False}])
        kinds = mh.plan_fetch(caps, has_images=True, oldnews=True, thumbnail=True)
        self.assertNotIn('image', kinds)
        self.assertNotIn('oldnews-page', kinds)
        self.assertEqual(kinds[-1], 'thumbnail')
        self.assertNotIn('image', mh.plan_fetch(mh.capabilities([]), has_images=True))

    def test_paid_with_images_plans_image(self):
        caps = mh.capabilities([{'signal': 'records', 'value': True}, {'signal': 'images', 'value': True}])
        self.assertIn('image', mh.plan_fetch(caps, has_images=True))
        self.assertNotIn('image', mh.plan_fetch(caps, has_images=False))
        caps['oldnews_pages'] = True
        self.assertEqual(mh.plan_fetch(caps, oldnews=True)[-2:], ['oldnews-page', 'oldnews-ocr'])


class Slugs(unittest.TestCase):
    def test_mirror_row_never_closes_familysearch(self):
        rows = [{'person_key': 'ana', 'person_label': '', 'source': mh.mirror_slug('30056'), 'query': 'ana zorvalo 1851',
                 'date': '2026-10-24', 'result': 'hit', 'evidence': '', 'agent': '', 'next_date': ''}]
        self.assertEqual(mh.mirror_slug('30056'), 'myheritage-espejo-fs-30056')
        self.assertEqual(gwt_ledger.find(rows, 'ana', 'familysearch', 'ana zorvalo 1851'), [])
        self.assertEqual(gwt_ledger.find(rows, 'ana', 'familysearch', 'ana zorvalo 1851', exact=True), [])
        self.assertEqual(len(gwt_ledger.find(rows, 'ana', 'myheritage-espejo-fs-30056', 'ana zorvalo 1851', exact=True)), 1)
        self.assertEqual(gwt_ledger.find(rows, 'ana', 'myheritage', 'ana zorvalo 1851'), [])


class Mirror(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(mh.mirror_verdict(20, 18, 18), 'equivalent')
        self.assertEqual(mh.mirror_verdict(20, 18, 17), 'partial')
        self.assertEqual(mh.mirror_verdict(20, 12, 12), 'partial')
        self.assertEqual(mh.mirror_verdict(20, 5, 5), 'not')
        self.assertEqual(mh.mirror_verdict(0, 0, 0), 'not')

    def test_basic_compares_only_shared_fields(self):
        fs = {'name': 'Ana Zorvalo Brineda', 'event': 'birth', 'year': 1851, 'place': 'Otrolugar', 'father': 'Pedro Zorvalo'}
        basic = mh.candidates_from_envelope(env_of('graphql_basic.json'))[0]
        found, agree, fields = mh.compare_mirror(fs, basic)
        self.assertEqual((found, agree, fields), (True, True, ['name', 'year']))
        paid = mh.candidates_from_envelope(env_of('graphql_paid.json'))[0]
        found, agree, fields = mh.compare_mirror(fs, paid)
        self.assertEqual((found, agree), (True, False), 'the paid plan shows the place, and it disagrees')
        self.assertIn('place', fields)
        self.assertEqual(mh.compare_mirror(dict(fs, year=1700), paid)[0], False)
        bare = [{'name': 'Ana Zorvalo Brineda', 'events': {'birth': {'year': 1851, 'year_to': 1851, 'place': ''}},
                 'relatives': [], 'mh': {'masked': []}}]
        self.assertEqual(mh.compare_mirror(fs, bare), (True, True, ['name', 'year']),
                         'a place the record does not give is not a disagreement')


class RecordPage(unittest.TestCase):
    def test_spanish(self):
        p = mh.parse_record_text(mh.html_to_text(load('record_es.html')))
        self.assertEqual(p['title'], 'Ana Zorvalo Brineda')
        self.assertEqual(p['collection'], 'Censo inventado de Villazul 1880')
        self.assertEqual(p['fields']['birth'], '12 de marzo de 1851\nVillazul, Murcia, España')
        self.assertEqual(p['provider'], 'Archivo Inventado')
        self.assertEqual(p['record_count'], 12345)
        self.assertEqual(p['collection_blurb'], 'Esta colección contiene un censo inventado para las pruebas.')
        self.assertFalse(p['oldnews_link'])
        c = mh.record_candidate(p, '20000', '42')
        self.assertEqual(c['events']['birth'], {'year': 1851, 'year_to': 1851, 'place': 'Villazul, Murcia, España'})
        for rel in ({'role': 'spouse', 'name': 'Tomás Quirell'}, {'role': 'father', 'name': 'Pedro Zorvalo'},
                    {'role': 'child', 'name': 'Luis Quirell Zorvalo'}):
            self.assertIn(rel, c['relatives'])
        if gwt_rank:
            self.assertEqual(gwt_rank._bad_record(c), '')

    def test_spanish_page_text(self):
        """The text the browser reads: the place sits on a line starting with a space and a no-break space."""
        p = mh.parse_record_text(load('record_es.txt'))
        self.assertEqual(p['fields']['birth'], '12 de marzo de 1851\nVillazul, Murcia, España')
        self.assertEqual(p['fields']['occupation'], 'Labradora')
        self.assertEqual(p['relatives'], [])
        c = mh.record_candidate(p, '20000', '43')
        self.assertEqual(c['events']['death'], {'year': 1910, 'year_to': 1910, 'place': 'Puertoalto'})
        self.assertEqual(p['record_count'], 12345)

    def test_english_oldnews(self):
        p = mh.parse_record_text(mh.html_to_text(load('record_en.html')))
        self.assertTrue(p['oldnews_link'] and p['oldnews_included'])
        self.assertEqual(p['mentioned'], ['Tomas Quirell', 'Rosa Velmar'])
        c = mh.record_candidate(p, '12040', '1-')
        self.assertEqual(c['source'], 'myheritage-oldnews')
        self.assertEqual(c['mh']['pub_place'], 'Villazul, Murcia, Spain')
        self.assertEqual(c['events']['birth']['year'], 1840)
        self.assertEqual(c['relatives'], [{'role': 'other', 'name': 'Rosa Velmar'}])

    def test_citation(self):
        s = mh.citation('Censo inventado', 'Ana Zorvalo', mh.HOST + '/research/collection-1/x?itemId=4&action=showRecord&perm_id=T',
                        '4', accessed='2026-10-24', provider='Archivo Inventado')
        self.assertIn('accessed 2026-10-24', s)
        self.assertNotIn('perm_id', s)


class Lists(unittest.TestCase):
    def test_matches(self):
        m = mh.parse_matches(load('matches_es.txt'))
        self.assertEqual([x['kind'] for x in m], ['Record Match', 'Smart Match'])
        self.assertEqual(m[0]['source'], 'Árbol Familiar FamilySearch')
        self.assertEqual(m[0]['name'], 'Ana Zorvalo (nacida Brineda)')
        self.assertEqual(m[0]['fields']['Padres'], 'Pedro Zorvalo, María Brineda Sol')

    def test_catalog(self):
        html = load('catalog.html')
        rows = mh.parse_catalog(html)
        self.assertEqual([r['id'] for r in rows], ['30056', '12040'], 'one row per collection')
        self.assertEqual(rows[0], {'id': '30056', 'title': 'España, Bautismos, 1502-1940',
                                   'category': 'Nacimiento, Matrimonio y Defunción', 'count': 1234567,
                                   'updated': '3 de mayo de 2021', 'has_images': ''})
        self.assertEqual(rows[1]['has_images'], 'yes')
        self.assertEqual(mh.catalog_pages(html), 35)
        self.assertEqual(mh.catalog_pages('<html></html>'), 1)
        self.assertEqual(mh.catalog_url(3), mh.HOST + '/research/records-catalog?p=3')
        self.assertEqual(mh.catalog_tsv(rows).splitlines()[0].split('\t'), list(mh.CATALOG_COLUMNS))


if __name__ == '__main__':
    unittest.main()
