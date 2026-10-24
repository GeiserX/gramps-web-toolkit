"""Tests for gwt-mh's pure logic. Synthetic fixtures only (invented people and ids; public collection ids),
no network, no browser. Run: cd myheritage && python3 -m unittest -v test_gwt_mh"""
import argparse, base64, contextlib, copy, io, json, os, subprocess, sys, tempfile, time, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
for sub in ('ledger', 'fscache', 'rank', 'gate'):
    sys.path.insert(0, os.path.join(HERE, '..', sub))
sys.path.insert(0, HERE)

import gwt_mh as mh  # noqa: E402
import gwt_ledger  # noqa: E402
from gwt_fscache import FSCache  # noqa: E402
from gwt_gate import Paused  # noqa: E402
import gwt_mh_browser as b  # noqa: E402

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

    def test_lockout_on_login_page_is_a_wall_not_dead(self):
        page = load('lockout_es.html')
        self.assertEqual(mh.classify(mh.HOST + '/login', mh.html_to_text(page), page), 'wall')
        self.assertEqual(mh.classify(mh.HOST + '/login', mh.html_to_text(page)), 'wall')
        en = 'Access temporarily disabled. Your access has been temporarily disabled due to irregular activity.'
        self.assertEqual(mh.classify(mh.HOST + '/login', en), 'wall')

    def test_spanish_imperva_hcaptcha_is_a_wall(self):
        page = load('imperva_es.html')
        self.assertEqual(mh.classify(mh.HOST + '/research?x', mh.html_to_text(page), page), 'wall')
        self.assertEqual(mh.classify(mh.HOST + '/research?x', 'CONTROL DE SEGURIDAD'), 'wall')
        self.assertEqual(mh.classify(mh.HOST + '/research?x', '', '<iframe src="https://hcaptcha.com/x">'), 'wall')

    def test_dead_paywall_and_signed_in_pages(self):
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

    def test_records_plan_tries_image_without_any_image_observation(self):
        caps = mh.capabilities([{'signal': 'records', 'value': True}])
        self.assertIsNone(caps['images'])
        self.assertIn('image', mh.plan_fetch(caps, has_images=True))

    def test_one_collection_without_image_does_not_turn_images_off_elsewhere(self):
        obs = [{'signal': 'records', 'value': True, 'at': '1'},
               {'signal': 'images', 'value': True, 'collection': '10', 'at': '2'},
               {'signal': 'images', 'value': False, 'collection': '20', 'at': '3'}]
        caps = mh.capabilities(obs)
        self.assertTrue(caps['images'])
        self.assertEqual(mh.image_collections(obs), {'10': True, '20': False})
        self.assertIn('image', mh.plan_fetch(caps, has_images=True))
        caps2 = mh.capabilities(obs[:1] + obs[2:])
        self.assertFalse(caps2['images'])
        self.assertIn('image', mh.plan_fetch(caps2, has_images=True))
        o = mh.record_observations(mh.HOST + '/research/collection-20/x?itemId=1', has_images=True, viewer=False,
                                   collection='20')
        self.assertEqual(o, [{'signal': 'images', 'value': False, 'url': '', 'collection': '20'}])

    def test_download_links_keep_record_images_only(self):
        hrefs = ['https://www.myheritage.es/download-app', 'https://www.myheritage.es/paywall?x=download',
                 'https://www.myheritage.es/FP/download.php?x=1', 'https://example.invalid/app.dmg?download=1',
                 'https://cf.myheritageimages.com/records/full/x_INVENTED.jpg?download=1',
                 'https://www.myheritage.es/research/record-image/download?itemId=1',
                 'https://www.myheritage.es/research/record-image/download?itemId=2',
                 'https://www.myheritage.es/research/record-image/download?itemId=3', 'javascript:void(0)']
        got = mh.image_download_links(hrefs)
        self.assertEqual(got, ['https://cf.myheritageimages.com/records/full/x_INVENTED.jpg?download=1',
                               'https://www.myheritage.es/research/record-image/download?itemId=1'])

    def test_download_links_reject_other_hosts_and_non_image_paths(self):
        for bad in ('https://www.myheritage.es/FP/API/export-gedcom?include_images=1&download=1',
                    'https://evil.example/record.jpg?download',
                    'https://www.myheritage.es/subscribe?plan=data&download=record',
                    'https://www.myheritage.es.evil.example/research/record-image/x.jpg',
                    'https://myheritageimages.com.evil.example/records/x.jpg',
                    'http://www.myheritage.es/research/record-image/download?itemId=1',
                    'https://www.myheritage.es/research/record/download?itemId=1',
                    'https://evilmyheritage.es/research/record-image/download?itemId=1',
                    'https://evilmyheritageimages.com/records/x.jpg',
                    'https://www.myheritage.es@evil.io/research/record-image/download?itemId=1',
                    'https://www.myheritage.zz/research/record-image/download?itemId=1',
                    'https://www.myheritage.co.zz/research/record-image/download?itemId=1'):
            self.assertEqual(mh.image_download_links([bad]), [], bad)
        self.assertEqual(mh.image_download_links(['https://www.myheritage.com/research/scan/download?itemId=1',
                                                  'https://www.myheritage.com.br/research/record-image/x']),
                         ['https://www.myheritage.com/research/scan/download?itemId=1',
                          'https://www.myheritage.com.br/research/record-image/x'])

    def test_cdn_links_skip_the_deny_list(self):
        """The image CDN only serves files; newspaper titles and signed tokens there hold deny-list letters."""
        for good in ('https://cf.myheritageimages.com/records/full/El_Exportador_1890/1890/01/01/0001.jpg',
                     'https://cf.myheritageimages.com/records/full/newbuyer_gazette/0001.jpg',
                     'https://cf.myheritageimages.com/records/full/x.jpg?sig=Abuyz09Kq&exp=1',
                     'https://cf.myheritageimages.com/records/full/x.jpg?token=qqUPGRADEqq'):
            self.assertEqual(mh.image_download_links([good]), [good], good)
        self.assertEqual(mh.image_download_links(['https://www.myheritage.es/research/record-image/El_Exportador']), [],
                         'on a MyHeritage site the deny list still applies')

    def test_userinfo_and_port(self):
        for bad in ('https://evil.io@www.myheritage.es/research/record-image/download?itemId=1',
                    'https://evil.io@cf.myheritageimages.com/records/full/x.jpg',
                    'https://evil.io\\@www.myheritage.es/research/record-image/download?itemId=1',
                    'https://evil.io\\.myheritage.es/image/x.jpg'):
            self.assertEqual(mh.image_download_links([bad]), [], bad)
            self.assertFalse(mh.is_mh_image_host(bad), bad)
        self.assertFalse(mh.is_oldnews_link('https://evil.io@www.oldnews.com/x'))
        port = 'https://www.myheritage.es:443/research/record-image/download?itemId=1'
        self.assertEqual(mh.image_download_links([port]), [port], 'the host is read without its port')
        self.assertTrue(mh.is_mh_image_host('https://cf.myheritageimages.com:443/records/x.jpg'))

    def test_host_option_adds_its_country_domain(self):
        saved = mh.MH_TLDS, mh._MH_HOST
        try:
            self.assertFalse(mh.is_mh_image_host('https://www.myheritage.ee/research/record-image/x'))
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(CLI.main(['--host', 'https://www.myheritage.ee', '--cache-dir', d, 'plan']), mh.EXIT_OK)
            self.assertTrue(mh.is_mh_image_host('https://www.myheritage.ee/research/record-image/x'))
            self.assertEqual(mh.image_download_links(['https://www.myheritage.ee/research/record-image/x']),
                             ['https://www.myheritage.ee/research/record-image/x'])
            before = mh.MH_TLDS
            mh.allow_host('https://evil.io')
            mh.allow_host('https://www.myheritage.es')
            self.assertEqual(mh.MH_TLDS, before)
        finally:
            mh.MH_TLDS, mh._MH_HOST = saved
        self.assertTrue(mh.is_mh_image_host('https://www.myheritage.at/research/record-image/x'))

    def test_hosts_by_hostname(self):
        self.assertTrue(mh.is_mh_image_host('https://cf.myheritageimages.com/x.jpg'))
        self.assertTrue(mh.is_mh_image_host('https://www.myheritage.de/research/record-image/x'))
        for bad in ('https://evil.example/x.jpg', 'https://www.myheritage.es@evil.io/x', 'http://www.myheritage.es/x',
                    'https://myheritage.es.evil.io/x', 'https://www.myheritage.zz/x'):
            self.assertFalse(mh.is_mh_image_host(bad), bad)
        self.assertTrue(mh.is_oldnews_link('https://www.oldnews.com/newspapers/x/page/1'))
        self.assertTrue(mh.is_oldnews_link('https://oldnews.com/x'))
        for bad in ('https://evil.example/?u=oldnews.com', 'https://oldnews.com.evil.io/x', 'https://evil.example/oldnews.com/x',
                    'https://www.oldnews.com@evil.io/x', 'https://notoldnews.com/x', 'http://www.oldnews.com/x'):
            self.assertFalse(mh.is_oldnews_link(bad), bad)

    def test_download_links_deny_list_is_what_stops_these(self):
        """On a MyHeritage host with image in the path, only the deny-list keeps these out."""
        for bad in ('https://www.myheritage.es/research/record-image/export-gedcom?itemId=1',
                    'https://www.myheritage.es/paywall/record-image?itemId=1',
                    'https://www.myheritage.es/research/record-image/checkout?itemId=1',
                    'https://www.myheritage.es/research/record-image?plan=trial',
                    'https://www.myheritage.es/image/subscription/download',
                    'https://www.myheritage.es/research/record-image/download-app',
                    'https://www.myheritage.es/scan/install'):
            self.assertEqual(mh.image_download_links([bad]), [], bad)

    def test_download_links_deny_camel_case_and_quoted(self):
        for path in ('/image/exportGedcom', '/imageExport/x', '/record-image/x?returnUrl=/FP/exportGedcom',
                     '/record-image/x?returnUrl=%2FFP%2FexportGedcom', '/record-image/x?next=%2FFP%2F%65xport%47edcom', '/images/buy-now?download',
                     '/scanner/purchase?download=1', '/record-image/upgradePlan', '/image/subscriptionOffer',
                     '/image/CheckOut'):
            self.assertEqual(mh.image_download_links(['https://www.myheritage.es' + path]), [], path)
        self.assertEqual(mh.image_download_links(['https://www.myheritage.es/research/record-image/application-form']),
                         ['https://www.myheritage.es/research/record-image/application-form'], 'app keeps its word edges')

    def test_bytes_answer_is_classified(self):
        img = slurp(os.path.join(FIX, 'tiny.png'), 'rb')
        u = 'https://cf.myheritageimages.com/records/full/x.jpg'
        self.assertEqual(mh.classify_bytes(u, 200, 'image/png', img), 'ok')
        self.assertEqual(mh.classify_bytes(u, 200, 'text/html', load('imperva.html').encode()), 'wall')
        self.assertEqual(mh.classify_bytes(u, 200, '', load('lockout_es.html').encode()), 'wall')
        self.assertEqual(mh.classify_bytes(u, 403, 'image/png', b''), 'wall')
        self.assertEqual(mh.classify_bytes(u, 401, '', b''), 'dead')
        self.assertEqual(mh.classify_bytes(u, 200, 'text/html', load('login_wall.html').encode()), 'dead')
        self.assertEqual(mh.classify_bytes(u, 404, 'image/png', b''), 'ok', 'a missing image is no wall')
        for ctype in ('', 'image/jpeg', 'application/octet-stream'):
            self.assertEqual(mh.classify_bytes(mh.HOST + '/login?returnUrl=x', 200, ctype, b''), 'dead', ctype)

    def test_paid_with_images_plans_image(self):
        caps = mh.capabilities([{'signal': 'records', 'value': True}, {'signal': 'images', 'value': True}])
        self.assertIn('image', mh.plan_fetch(caps, has_images=True))
        self.assertNotIn('image', mh.plan_fetch(caps, has_images=False))
        caps['oldnews_pages'] = True
        self.assertEqual(mh.plan_fetch(caps, oldnews=True)[-2:], ['oldnews-page', 'oldnews-ocr'])


class SessionLock(unittest.TestCase):
    def test_second_session_on_one_account_is_busy(self):
        with tempfile.TemporaryDirectory() as d:
            mk = lambda lane: b.Session('', 'ana', os.path.join(d, 'c'), os.path.join(d, 'g'), lane, log=lambda m: None,
                                          lock_dir=os.path.join(d, 'locks'))
            one, two = mk('a'), mk('b')
            one.acquire()
            with self.assertRaises(b.Busy) as cm:
                two.acquire()
            self.assertEqual(cm.exception.exit_code, mh.EXIT_BUSY)
            one.release()
            two.acquire()
            two.release()

    def test_lock_is_keyed_on_the_account_not_the_gate_dir(self):
        with tempfile.TemporaryDirectory() as d:
            mk = lambda acct, gate: b.Session('', acct, os.path.join(d, 'c'), os.path.join(d, gate), 'l',
                                              log=lambda m: None, lock_dir=os.path.join(d, 'locks'))
            one = mk('ana', 'g1')
            one.acquire()
            with self.assertRaises(b.Busy):
                mk('ana', 'g2').acquire()
            other = mk('luis', 'g1')
            other.acquire()
            other.release()
            one.release()

    def test_exit_codes_are_distinct(self):
        codes = [mh.EXIT_OK, mh.EXIT_EMPTY, mh.EXIT_WALL, mh.EXIT_DEAD, mh.EXIT_PAYWALL, mh.EXIT_PAUSED, mh.EXIT_BUSY]
        self.assertEqual(len(set(codes)), len(codes))
        self.assertEqual((b.Wall.exit_code, b.Busy.exit_code), (mh.EXIT_WALL, mh.EXIT_BUSY))
        h = subprocess.run([sys.executable, os.path.join(HERE, 'gwt-mh'), '--help'], capture_output=True, text=True).stdout
        for line in ('3 bot wall', '6 the account', '7 another'):
            self.assertIn(line, ' '.join(h.split()))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakePage:
    """A record page: its links, and the answer an in-page fetch gets for each URL."""
    url = mh.HOST + '/research/collection-40001/x?itemId=9000002&action=showRecord'

    def __init__(self, hrefs=(), answers=None):
        self.hrefs, self.answers, self.fetched, self.shots = list(hrefs), answers or {}, [], []

    def eval_on_selector_all(self, sel, js):
        return list(self.hrefs)

    def evaluate(self, js, url):
        self.fetched.append(url)
        st, ctype, data, *final = self.answers.get(url, (404, 'text/plain', b''))
        return {'status': st, 'type': ctype, 'b64': base64.b64encode(data).decode(), 'url': final[0] if final else url}

    def screenshot(self, path, full_page=False):
        self.shots.append(path)
        with open(path, 'wb') as f:
            f.write(b'png')


def offline_session(d, page, load_env=None, t=1_000_000.0):
    """A real Session (real gate, cache and plan state) on a fake clock and a fake page; no browser."""
    S = b.Session('', 'ana', os.path.join(d, 'c'), os.path.join(d, 'g'), 'lane', rest_every=0, jitter=0,
                  log=lambda m: None, lock_dir=os.path.join(d, 'locks'))
    clk = Clock(t)
    S.gate.clock, S.gate.sleep, S.clock = clk, clk.sleep, clk
    S.page = page
    S.loaded = []
    if load_env is not None:
        def _load(url, wait_graphql=False):
            S.loaded.append(url)
            return dict(load_env, url=url, captured_at=mh.now_iso(), plan=S.plan.label())
        S._load = _load
    return S, clk


def record_env(text=None):
    text = text if text is not None else mh.html_to_text(load('record_es.html'))
    return {'final_url': FakePage.url, 'text': text, 'html': '<html><body>record</body></html>', 'graphql': []}


class WallBlocks(unittest.TestCase):
    def test_wall_blocks_the_gate_for_24_hours(self):
        with tempfile.TemporaryDirectory() as d:
            lock = load('lockout_es.html')
            S, clk = offline_session(d, FakePage(), dict(record_env(mh.html_to_text(lock)), final_url=mh.HOST + '/login', html=lock))
            t0 = clk.t
            with self.assertRaises(b.Wall):
                S.capture(mh.record_url('40001', '9000002'))
            for later in (1201, 6 * 3600, 23.9 * 3600):
                clk.t = t0 + later
                with self.assertRaises(Paused, msg='%d s after a wall' % later):
                    S.gate.wait('another-lane')
            clk.t = t0 + 24 * 3600 + 1
            S.gate.wait('another-lane')

    def test_wall_in_an_image_download_stops_the_fetch(self):
        """cmd_fetch: the filter picks the links, a wall on the first stops everything after it."""
        good = 'https://cf.myheritageimages.com/records/full/x_INVENTED.jpg?download=1'
        nxt = 'https://www.myheritage.es/research/record-image/download?itemId=9000002'
        page = FakePage(hrefs=['https://evil.example/record.jpg?download', good, nxt],
                        answers={good: (403, 'text/html', load('imperva.html').encode()),
                                 nxt: (200, 'image/png', slurp(os.path.join(FIX, 'tiny.png'), 'rb'))})
        with tempfile.TemporaryDirectory() as d:
            S, clk = offline_session(d, page, record_env())
            a = fetch_args(d)
            t0 = clk.t
            with self.assertRaises(b.Wall):
                CLI.cmd_fetch(S, a)
            self.assertEqual(page.fetched, [good], 'nothing after the wall, never the other host, never the thumbnail')
            self.assertEqual(page.shots, [])
            clk.t = t0 + 23 * 3600
            with self.assertRaises(Paused):
                S.gate.wait('lane')

    def test_login_page_for_an_image_is_dead(self):
        good = 'https://cf.myheritageimages.com/records/full/x_INVENTED.jpg?download=1'
        page = FakePage(hrefs=[good], answers={good: (200, 'text/html', load('login_wall.html').encode())})
        with tempfile.TemporaryDirectory() as d:
            S, _ = offline_session(d, page, record_env())
            with self.assertRaises(b.Dead):
                CLI.cmd_fetch(S, fetch_args(d))


def _load_cli():
    import importlib.machinery, importlib.util
    loader = importlib.machinery.SourceFileLoader('gwt_mh_cli', os.path.join(HERE, 'gwt-mh'))
    spec = importlib.util.spec_from_loader('gwt_mh_cli', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


CLI = _load_cli()


def fetch_args(d):
    doc = copy.deepcopy(load('graphql_paid.json'))
    for x in mh.graphql_records({'graphql': doc})[0]:
        x['record']['collection']['has_images'] = True
    search = os.path.join(d, 'search.json')
    mh.write_json(search, {'graphql': doc})
    return argparse.Namespace(collection='40001', item='9000002', search=search, out=os.path.join(d, 'out'),
                              no_rawstore=True, host=mh.HOST)


class FetchWiring(unittest.TestCase):
    def test_fetch_keeps_the_image_and_records_its_collection(self):
        good = 'https://www.myheritage.es/research/record-image/download?itemId=9000002'
        bad = 'https://www.myheritage.es/FP/API/export-gedcom?include_images=1&download=1'
        png = slurp(os.path.join(FIX, 'tiny.png'), 'rb')
        page = FakePage(hrefs=[bad, 'https://evil.example/record.jpg?download', good],
                        answers={good: (200, 'image/png', png), bad: (200, 'image/png', png)})
        with tempfile.TemporaryDirectory() as d:
            S, _ = offline_session(d, page, record_env())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(CLI.cmd_fetch(S, fetch_args(d)), mh.EXIT_OK)
            self.assertEqual(page.fetched[0], good, 'cmd_fetch must pass the page links through the filter')
            self.assertNotIn(bad, page.fetched)
            self.assertEqual(slurp(os.path.join(d, 'out', '40001', '9000002', 'image.png'), 'rb'), png)
            imgs = [o for o in S.plan.observations() if o['signal'] == 'images']
            self.assertEqual([(o['value'], o['collection']) for o in imgs], [(True, '40001')])


@contextlib.contextmanager
def fake_playwright(calls):
    """playwright.sync_api replaced by a fake whose Chrome already has another tool's context open."""
    class Ctx:
        def __init__(self, name):
            self.name = name

        def new_page(self):
            calls.append(('new_page', self.name))
            return types.SimpleNamespace(on=lambda *x: None, close=lambda: None)

        def close(self):
            calls.append(('close', self.name))

    class Browser:
        contexts = [Ctx('theirs')]

        def new_context(self, **kw):
            calls.append(('new_context',))
            return Ctx('ours')

    pw = types.SimpleNamespace(chromium=types.SimpleNamespace(connect_over_cdp=lambda url: Browser()),
                               stop=lambda: calls.append(('stop',)))
    api = types.ModuleType('playwright.sync_api')
    api.sync_playwright = lambda: types.SimpleNamespace(start=lambda: pw)
    saved = {k: sys.modules.get(k) for k in ('playwright', 'playwright.sync_api')}
    sys.modules['playwright'], sys.modules['playwright.sync_api'] = types.ModuleType('playwright'), api
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class NewContext(unittest.TestCase):
    def test_session_opens_its_own_context_never_an_existing_one(self):
        calls = []
        with fake_playwright(calls), tempfile.TemporaryDirectory() as d:
            with b.Session('http://cdp.invalid', 'ana', os.path.join(d, 'c'), os.path.join(d, 'g'), 'l',
                           log=lambda m: None, lock_dir=os.path.join(d, 'locks')) as S:
                self.assertEqual(S._ctx.name, 'ours')
        self.assertIn(('new_page', 'ours'), calls)
        self.assertFalse([c for c in calls if 'theirs' in c], "another tool's context is never used or closed")

    def test_blocked_gate_exits_6_before_any_page_load(self):
        calls = []
        with fake_playwright(calls), tempfile.TemporaryDirectory() as d:
            g = os.path.join(d, 'g')
            b.Gate(g).block(24, 'a wall')
            old, b.LOCK_DIR = b.LOCK_DIR, os.path.join(d, 'locks')
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    code = CLI.main(['--cdp', 'http://cdp.invalid', '--gate-dir', g, '--cache-dir', os.path.join(d, 'c'),
                                     'search', '--given', 'Ana', '--surname', 'Zorvalo', '--out', d])
            finally:
                b.LOCK_DIR = old
        self.assertEqual(code, mh.EXIT_PAUSED)


class Retry(unittest.TestCase):
    def test_context_client_retry_takes_its_own_gate_slot(self):
        url = 'https://cf.myheritageimages.com/records/full/x_INVENTED.jpg'
        png = slurp(os.path.join(FIX, 'tiny.png'), 'rb')

        class CorsPage(FakePage):
            def evaluate(self, js, u):
                raise RuntimeError('TypeError: Failed to fetch')

        with tempfile.TemporaryDirectory() as d:
            S, _ = offline_session(d, CorsPage())
            S._ctx = types.SimpleNamespace(request=types.SimpleNamespace(
                get=lambda u, **kw: types.SimpleNamespace(status=200, headers={'content-type': 'image/png'}, body=lambda: png, url=u)))
            self.assertEqual(S.fetch_bytes(url), (200, 'image/png', png))
            calls = [ln for ln in S.gate.last_calls(50) if ' call ' in ln]
            self.assertEqual(len(calls), 2, 'the in-page fetch and the retry each wait for a slot')



class AccountWall(unittest.TestCase):
    def test_wall_blocks_the_account_whatever_the_gate_dir(self):
        calls = []
        with fake_playwright(calls), tempfile.TemporaryDirectory() as d:
            lock = load('lockout_es.html')
            S, clk = offline_session(d, FakePage(), dict(record_env(mh.html_to_text(lock)), final_url=mh.HOST + '/login',
                                                         html=lock), t=time.time())
            with self.assertRaises(b.Wall):
                S.capture(mh.record_url('40001', '9000002'))
            old, b.LOCK_DIR = b.LOCK_DIR, os.path.join(d, 'locks')
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    code = CLI.main(['--cdp', 'http://cdp.invalid', '--account', 'ana', '--gate-dir', os.path.join(d, 'other-gate'),
                                     '--cache-dir', os.path.join(d, 'c'), 'search', '--given', 'Ana', '--surname', 'Zorvalo',
                                     '--out', d])
            finally:
                b.LOCK_DIR = old
            self.assertEqual(code, mh.EXIT_PAUSED)
            self.assertEqual(calls, [], 'no browser is even attached')
            later = b.Session('', 'ana', os.path.join(d, 'c'), os.path.join(d, 'g3'), 'l', log=lambda m: None,
                              lock_dir=os.path.join(d, 'locks'))
            later.clock = lambda: clk.t + 23.9 * 3600
            with self.assertRaises(Paused):
                later.acquire()
            later.clock = lambda: clk.t + 24 * 3600 + 1
            later.acquire()
            later.release()
            other = b.Session('', 'luis', os.path.join(d, 'c'), os.path.join(d, 'g4'), 'l', log=lambda m: None,
                              lock_dir=os.path.join(d, 'locks'))
            other.acquire()
            other.release()


class Redirects(unittest.TestCase):
    def test_download_redirected_off_the_allowed_hosts_is_dropped(self):
        first = 'https://www.myheritage.es/research/record-image/download?itemId=1'
        second = 'https://cf.myheritageimages.com/records/full/x_INVENTED.jpg?download=1'
        png = slurp(os.path.join(FIX, 'tiny.png'), 'rb')
        page = FakePage(hrefs=[first, second], answers={first: (200, 'image/png', b'EVIL' + png, 'https://evil.example/x.png'),
                                                        second: (200, 'image/png', png)})
        with tempfile.TemporaryDirectory() as d:
            S, _ = offline_session(d, page, record_env())
            logs = []
            S.log = logs.append
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(CLI.cmd_fetch(S, fetch_args(d)), mh.EXIT_OK)
            self.assertEqual(page.fetched[:2], [first, second])
            self.assertTrue([m for m in logs if m.startswith('dropped') and 'host evil.example' in m], logs)
            self.assertEqual(slurp(os.path.join(d, 'out', '40001', '9000002', 'image.png'), 'rb'), png)


class FinalUrl(unittest.TestCase):
    def test_download_redirected_to_login_without_markers_is_dead(self):
        url = 'https://cf.myheritageimages.com/records/full/x_INVENTED.jpg'
        page = FakePage(answers={url: (200, '', b'', mh.HOST + '/login?returnUrl=%2Fresearch')})
        with tempfile.TemporaryDirectory() as d:
            S, _ = offline_session(d, page)
            with self.assertRaises(b.Dead):
                S.fetch_bytes(url, final_ok=mh.is_mh_image_host)


class OldNewsLink(unittest.TestCase):
    def test_fetch_opens_only_an_oldnews_host(self):
        good = 'https://www.oldnews.com/newspapers/el-eco/page/3'
        page = FakePage(hrefs=['https://evil.example/?u=oldnews.com', 'https://oldnews.com.evil.io/x', good])
        with tempfile.TemporaryDirectory() as d:
            S, _ = offline_session(d, page, dict(record_env(mh.html_to_text(load('record_en.html'))),
                                                 final_url=mh.HOST + '/research/collection-12040/x?itemId=800001-&action=showRecord'))
            S.plan.add([{'signal': 'records', 'value': True}, {'signal': 'oldnews_pages', 'value': True}])
            search = os.path.join(d, 'search.json')
            mh.write_json(search, {'graphql': load('graphql_oldnews.json')})
            a = argparse.Namespace(collection='12040', item='800001-', search=search, out=os.path.join(d, 'out'),
                                   no_rawstore=True, host=mh.HOST)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                CLI.cmd_fetch(S, a)
            self.assertIn(good, S.loaded)
            self.assertFalse([u for u in S.loaded if 'evil' in u], S.loaded)


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
