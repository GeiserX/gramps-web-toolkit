#!/usr/bin/env python3
"""_template: copy this file to read/gwt_<id>.py to add a record site to gwt-read. It reads an invented site.

A source is one module with:

    SOURCE      the slug: the ledger's source and each candidate's `source`
    SPEC        what gwt-read builds from (checked by gwt_reader.spec_problems):
                  id                the same slug; the subcommand is `gwt-read <id>`
                  help              one line for `gwt-read --help`
                  pace              {start, floor, ceil} seconds between requests, for the source's own gate
                  cache_rules       ((url regex, seconds or None), ...): how long gwt-fscache keeps each answer
                  controls          [{argv: [search options], min_count: N}]: canary searches any working copy of the
                                    site answers with at least N rows. Public and neutral: a very common name, never
                                    a family's own surname
                  result_markers    regexes found only on a page with at least one row of results
                  negative_markers  regexes of the site's own words for "nothing found"
    add_search_args(sp)  the options of `search` (--out is added for you)
    search(a, R)         runs one search through R (a gwt_reader.Reader), writes the files, prints the lines, and
                         returns 0 with rows, 2 with none
    add_record_args(sp), record(a, R)   optional, the same for one record by its id

Then name the module in gwt_reader.SOURCE_MODULES, add its line to pyproject.toml's force-include, write
read/test_gwt_<id>.py with invented fixture pages (test_registry.py tests this template the same way), add mutant
rows to read/mutants.d/<id>.txt, and a page docs/sources/<id>.md. See docs/sources/index.md.

Every request goes through R.get with page=gwt_reader.page_check(SPEC): a page with no row and without the
site's own "nothing found" words (an empty skeleton, a block page, a new wording) is a STOP, exit 3, never
"no results". Standard library only.
"""
import html, re, urllib.parse

import gwt_reader

HOST = 'https://records.example.org'
SOURCE = 'example'
SPEC = {
    'id': SOURCE,
    'help': 'an invented record site, the template for a new source',
    'pace': {'start': 15.0, 'floor': 10.0, 'ceil': 120.0},
    'cache_rules': ((r'/search\?', 7 * 86400), (r'/record/', None)),
    'controls': [{'argv': ['--surname', 'Smith'], 'min_count': 3}],
    'result_markers': [r'<li class="hit"'],
    'negative_markers': [r'No records match'],
}
ACCEPT = 'text/html'

_HIT = re.compile(r'<li class="hit" data-id="(\w+)">(.*?)</li>', re.S)


def text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def search_url(given='', surname=''):
    return HOST + '/search?' + urllib.parse.urlencode([('given', given), ('surname', surname)])


def candidates(page):
    return [{'source': SOURCE, 'id': rid, 'name': text(name), 'role': 'principal', 'principal': True,
             'given': None, 'surnames': None, 'events': {}, 'relatives': [], 'url': HOST + '/record/' + rid}
            for rid, name in _HIT.findall(page)]


def add_search_args(sp):
    sp.add_argument('--given', default='')
    sp.add_argument('--surname', default='')


def search(a, R):
    url = search_url(a.given, a.surname)
    status, rh, body, cached = R.get(url, {'accept': ACCEPT}, refresh=a.refresh, page=gwt_reader.page_check(SPEC))
    page = body.decode('utf-8', 'replace')
    cands = candidates(page)
    env = gwt_reader.envelope(url, None, text=text(page), verdict=gwt_reader.classify(page, SPEC), from_cache=cached)
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, [SOURCE, a.given, a.surname]), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    print('results\t%d read%s' % (len(cands), ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
