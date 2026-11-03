"""Tests for `gwt-read freeukgen` (FreeREG and FreeCEN). Pages in fixtures/freeukgen/ follow the markup of the sites'
open-source templates (MyopicVicar) with invented people and ids; a fake transport and a fake gate: no request leaves
this machine. Run: cd read && python3 -m pytest -q test_gwt_freeukgen.py"""
import contextlib, io, json, os, unittest, unittest.mock, urllib.parse, urllib.request

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_freeukgen  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'freeukgen')
TOKEN = 'INVENTED-form-token-0123456789abcdef=='
REG = 'https://www.freereg.org.uk'
CEN = 'https://www.freecen.org.uk'


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def search(self, net, gate, *extra, county=('--county', 'DEV')):
        """A FreeREG search needs a county (or a type and both years): DEV unless a test says otherwise."""
        o, e = io.StringIO(), io.StringIO()
        extra = extra if '--county' in extra else extra + tuple(county)
        argv = ['freeukgen', '--cache-dir', self.cache, '--lane', 't', 'search', '--surname', 'Wexcombe', *extra,
                '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()


class Shape(unittest.TestCase):
    def test_three_states(self):
        spec = gwt_freeukgen.SPEC
        self.assertEqual(gwt_reader.classify(fx('results-freereg.html').decode(), spec), 'hits')
        self.assertEqual(gwt_reader.classify(fx('results-freecen.html').decode(), spec), 'hits')
        self.assertEqual(gwt_reader.classify(fx('none.html').decode(), spec), 'none')
        self.assertEqual(gwt_reader.classify(fx('changed.html').decode(), spec), 'unknown')
        self.assertEqual(gwt_reader.classify(fx('maintenance.html').decode(), spec), 'unknown')

    def test_the_form_fields(self):
        f = gwt_freeukgen.search_fields('freereg', 'Wexcombe', '', 1800, '', 'baptisms', ['DEV', 'CON'])
        self.assertEqual(f, [('search_query[last_name]', 'Wexcombe'), ('search_query[start_year]', '1800'),
                             ('search_query[chapman_codes][]', 'DEV'), ('search_query[chapman_codes][]', 'CON'),
                             ('search_query[record_type]', 'ba'), ('search_query[region]', '')])

    def test_the_pace(self):
        self.assertGreaterEqual(gwt_freeukgen.SPEC['pace']['floor'], 5.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_freeukgen.SPEC), [])


class Search(Run):
    def results(self, name, site=REG):
        return (200, fx(name), site + '/search_queries/0123456789abcdef0000aaaa')

    def test_the_form_then_the_search_as_a_person_fills_it_in(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('form.html')), self.results('results-freereg.html'))
        code, lines, text = self.search(net, gate, '--given', 'Thomas', '--type', 'baptisms', '--county', 'DEV')
        self.assertEqual(code, 0, text)
        form, post = net.requests
        self.assertEqual((form.get_method(), form.full_url), ('GET', REG + '/search_queries/new'))
        self.assertEqual((post.get_method(), post.full_url), ('POST', REG + '/search_queries'))
        sent = urllib.parse.parse_qs(post.data.decode(), keep_blank_values=True)
        self.assertEqual(sent['authenticity_token'], [TOKEN])
        self.assertEqual((sent['search_query[last_name]'], sent['search_query[first_name]'],
                          sent['search_query[record_type]'], sent['search_query[chapman_codes][]'],
                          sent['search_query[region]']), (['Wexcombe'], ['Thomas'], ['ba'], ['DEV'], ['']))
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['id'], a['site'], a['name'], a['url']),
                         ('0123456789abcdef01234567', 'freereg', 'Thomas Wexcombe',
                          REG + '/search_records/0123456789abcdef01234567/thomas-wexcombe'))
        self.assertEqual(a['events'], {'Baptism': {'date': '12 Mar 1801', 'place': 'Ashwick : St Brannoc : Parish Register',
                                                   'county': 'Devon'}})
        self.assertEqual(b['relatives'], [{'role': 'also named', 'name': 'Mary Pellow'}])
        env = json.load(open(lines['evidence']))
        self.assertEqual(env['final_url'], REG + '/search_queries/0123456789abcdef0000aaaa')
        for root, _, files in os.walk(self.out):
            for n in files:
                self.assertNotIn(TOKEN.encode(), open(os.path.join(root, n), 'rb').read(), n)

    def test_a_census_row(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('form.html').replace(b'FreeREG', b'FreeCEN')), self.results('results-freecen.html', CEN))
        code, lines, text = self.search(net, gate, '--site', 'freecen', '--type', '1851')
        self.assertEqual(code, 0, text)
        self.assertTrue(net.requests[1].full_url.startswith(CEN))
        (c,) = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((c['name'], c['events']['census'], c['events']['birth']),
                         ('Jane Wexcombe', {'year': '1851', 'county': 'Cornwall', 'district': 'Porthvean (St Kew)'},
                          {'year': '1822', 'place': 'Porthvean', 'county': 'Cornwall'}))

    def test_the_sites_no_results_heading_is_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fx('form.html')), self.results('none.html')), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))

    def test_too_many_results_is_neither_rows_nor_none(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, fx('form.html')), self.results('too-many.html')), gate)
        self.assertEqual((code, gate.hits, self.out_files()), (gwt_reader.EXIT_ERROR, [], []), text)
        self.assertIn('too many results', text)

    def test_a_changed_layout_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, fx('form.html')), self.results('changed.html')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_the_maintenance_page_is_a_block(self):
        gate = FakeGate()
        net = FakeNet(gate, (503, fx('maintenance.html')))
        code, _, text = self.search(net, gate)
        self.assertEqual((code, len(gate.hits), len(net.requests), self.out_files()), (gwt_reader.EXIT_WALL, 1, 1, []), text)

    def test_a_form_without_its_token_is_a_stop_and_nothing_is_posted(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, b'<html><body><form action="/search_queries"></form></body></html>'))
        code, _, text = self.search(net, gate)
        self.assertEqual((code, len(net.requests), len(gate.hits)), (gwt_reader.EXIT_WALL, 1, 1), text)

    def test_the_form_is_read_fresh_every_time(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('form.html')), self.results('none.html'), (200, fx('form.html')),
                      self.results('none.html'))
        self.search(net, gate)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 4, 'the form and its token came from the cache')

    def test_a_type_the_site_does_not_have_is_refused(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, text = self.search(net, gate, '--site', 'freecen', '--type', 'baptisms')
        self.assertEqual((code, net.requests), (64, []), text)

    def test_a_record_row_the_reader_cannot_read_is_a_stop_never_none(self):
        page = fx('results-freereg.html').replace(b'</tr>', b'')
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('form.html')), (200, page, REG + '/search_queries/0123456789abcdef0000aaaa'))
        code, _, text = self.search(net, gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def refused(self, *extra, county=()):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, text = self.search(net, gate, *extra, county=county)
        self.assertEqual((code, net.requests, gate.hits), (64, [], []), (extra, text))
        return text

    def test_what_the_site_would_refuse_is_refused_before_any_request(self):
        self.assertIn('date range and record type', self.refused())
        self.assertIn('date range and record type', self.refused('--from', '1800', '--to', '1801'))
        self.assertIn('more than 3 counties', self.refused(*'--county DEV --county CON --county SOM --county DOR'.split()))
        self.assertIn('no start year', self.refused('--to', '1800', county=('--county', 'DEV')))
        self.assertIn('precede', self.refused('--from', '1810', '--to', '1800', county=('--county', 'DEV')))
        self.assertIn('surname', self.refused('--surname', '', '--given', 'Thomas', county=('--county', 'DEV')))

    def test_a_year_alone_or_not_a_number_is_refused_before_any_request(self):
        self.assertIn('no end year', self.refused('--from', '1800', county=('--county', 'DEV')))
        self.assertIn('a year is a number', self.refused('--from', '18x0', '--to', '1900', county=('--county', 'DEV')))

    def test_what_the_site_accepts_is_sent(self):
        for extra in (('--type', 'baptisms', '--from', '1800', '--to', '1801'),
                      tuple('--county ALD --county GSY --county JSY --county SRK'.split()),
                      ('--site', 'freecen')):
            gate = FakeGate()
            net = FakeNet(gate, (200, fx('form.html')), self.results('none.html'))
            code, _, text = self.search(net, gate, *extra, county=())
            self.assertEqual((code, len(net.requests)), (gwt_reader.EXIT_EMPTY, 2), (extra, text))

    def test_the_form_shown_again_with_the_sites_errors_is_a_usage_error_not_a_block(self):
        page = fx('form.html').replace(b'<form', b'<h2 class="gamma text--center">1 error prohibited this search from '
                                       b'being processed:</h2><ul class="validation-list"><li class="validation-list__'
                                       b'error text--center" >Two letters must precede any wildcard in a surname.</li>'
                                       b'</ul><form', 1)
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, fx('form.html')), (200, page)), gate)
        self.assertEqual((code, gate.hits, self.out_files()), (64, [], []), text)
        self.assertIn('Two letters must precede any wildcard in a surname.', text)

    def test_the_session_cookie_lives_in_a_cookie_jar(self):
        handlers = []
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('form.html')), self.results('none.html'))

        def build_opener(*hs):
            handlers.extend(hs)
            return unittest.mock.Mock(open=net)
        plain = unittest.mock.Mock(side_effect=AssertionError('a request without the cookie jar'))
        with unittest.mock.patch.object(urllib.request, 'build_opener', build_opener), \
                unittest.mock.patch.object(urllib.request, 'urlopen', plain), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = gwt_read.main(['freeukgen', '--cache-dir', self.cache, 'search', '--surname', 'Wexcombe',
                                  '--county', 'DEV', '--out', self.out], gate=gate)
        self.assertEqual((code, len(net.requests)), (gwt_reader.EXIT_EMPTY, 2))
        self.assertTrue(any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in handlers), handlers)


if __name__ == '__main__':
    unittest.main()
