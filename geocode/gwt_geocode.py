#!/usr/bin/env python3
"""gwt-geocode: coordinates for the places a tree's events use, as a gwt-apply change list.

  gwt-geocode --snapshot DUMP --out changes.jsonl [--recheck] [--providers LIST] [--overrides FILE]
              [--geonames FILE ...] [--country NAME=CC ...] [--contact EMAIL] [--cache DIR | --no-cache]
              [--ruling ID] [--lang CODE] > report.tsv

Reads an offline `gwt-check dump` or `gwt-apply` snapshot and never writes to the tree. A point already in the tree is
kept unless --recheck asks again. Only places that some event uses get a point: a country, a province or a town that only holds other places gets none, so the Gramps Web map shows
one marker per event place. Parents are still looked up in memory, as the reference a child is checked against.

Each place is looked up by its own name and the names above it, through a chain of providers (--providers, in order):
cartociudad (Spain), ban (France), geonames-dump (a GeoNames dump file you downloaded, any country), nominatim
(OpenStreetMap, anywhere; at most one request a second). The first provider that gives an accepted answer wins.
Rules:
  - a town must carry the place's name; with a town above it, it must lie within 30 km of that town;
  - a church, building, cemetery or other spot must lie within 5 km of its town (a street within 2 km), else it gets
    the town's point and the report says why;
  - a spot's name must end the same way as the gazetteer's name once church abbreviations are written out
    (gwt_core.rules.dedication): «Iglesia de San Ejemplo» matches «San Ejemplo», never «San Ejemplo Obrero»;
  - a result in another country than the hierarchy's is refused;
  - an --overrides row (gramps_id, lat, long, note) always wins.
Output: update_place ops with lat and long on --out, and a TSV report on stdout, one row per event place and per
container place that has a point. Exit 2 on a bad input file or an --out that cannot be written, 3 when no provider
answered a single request. Standard library only.
"""
import argparse, gzip, hashlib, http.client, io, json, math, os, ssl, sys, time, urllib.error, urllib.parse, \
    urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from gwt_core import rules
except ModuleNotFoundError:  # a clone: the shared core sits next to this folder
    sys.path.insert(0, os.path.join(HERE, '..', 'core'))
    from gwt_core import rules

SPOT_KM = 5.0       # a church, building or cemetery: at most this far from its town
STREET_KM = 2.0     # a street or a house number
LOCALITY_KM = 30.0  # a town under another town (a hamlet in a municipality)
AMBIGUOUS_KM = 10.0  # two answers of the same name further apart than this, and nothing to choose by: no answer
MOVE_KM = 0.05      # with --recheck, a point already in the tree is moved only when the answer is further than this
AREA = {'Country', 'State', 'Province', 'Region', 'Department', 'County'}
SETTLEMENT = {'City', 'Town', 'Village', 'Hamlet', 'Municipality', 'Locality', 'Neighborhood', 'Borough', 'District',
              'Unknown', ''}
STREET = {'Street', 'Number'}
LOCAL = {'Parish', 'Farm'}  # a church or a farm under a town; the settlement itself when no town is above
PROVIDERS = ('cartociudad', 'ban', 'geonames-dump', 'nominatim')
# the language whose church abbreviations a country's names use; any other country reads English ones
LANG_OF = {'es': 'es', 'mx': 'es', 'ar': 'es', 'cu': 'es', 'uy': 'es', 'cl': 'es', 'co': 'es', 'pe': 'es',
           'fr': 'fr', 'be': 'fr', 'it': 'it', 'pt': 'pt', 'br': 'pt', 'de': 'de', 'at': 'de', 'nl': 'nl'}
OSM_TOWN = {'city', 'town', 'village', 'hamlet', 'suburb', 'neighbourhood', 'quarter', 'locality',
            'isolated_dwelling', 'municipality', 'borough', 'city_district'}
OSM_AREA = {'country', 'state', 'province', 'county', 'region', 'state_district', 'department'}
GEONAMES_TOWN_A = {'ADM3', 'ADM4', 'ADM5'}  # a municipality in GeoNames' admin classes
COLUMNS = ['gramps_id', 'place', 'type', 'match', 'provider', 'confidence', 'distance_km', 'lat', 'long', 'note']
UA = 'gramps-web-toolkit-geocode (+https://github.com/GeiserX/gramps-web-toolkit)'


class BadInput(Exception):
    pass


class ProviderError(Exception):
    pass


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def text_of(v):
    return ((v.get('string') or v.get('value')) if isinstance(v, dict) else v) or ''


def name_forms(name):
    """«Villanueva (Vilanova)» -> both names; «Ejemplo/Exemple» -> both."""
    out = []
    for part in name.replace(')', '').split('('):
        out += [p for p in part.split('/') if p.strip()]
    return {rules.fold(n) for n in out if rules.fold(n)}


def same_name(place, names):
    """The town carries the place's name: equal once folded, in any of the forms either side lists."""
    mine = name_forms(place)
    return any(mine & name_forms(n) for n in names if n)


def same_ending(place, name, lang, town=''):
    """A spot's name and the gazetteer's end with the same words, with church abbreviations written out, or differ
    only in their first word; so «Iglesia de San Ejemplo» matches «San Ejemplo» and «Parroquia de San Ejemplo», never
    «San Ejemplo Obrero» or «Parroquia de San Ejemplo Obrero»."""
    a, b = rules.dedication(place, lang).split(), rules.dedication(name, lang).split()
    short, long_ = sorted((a, b), key=len)
    if not short:
        return False
    if a == b or (len(a) >= 3 and a[1:] == b[1:]):  # the same name after another head word: Iglesia / Parroquia
        return True
    return len(short) >= 2 and long_[-len(short):] == short and short != rules.fold(town).split()


# ---- input -------------------------------------------------------------------------------------------------------

def load_snapshot(path):
    try:
        with open(path, 'rb') as f:
            gz = f.read(2) == b'\x1f\x8b'
        with (gzip.open if gz else open)(path, 'rt', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError, EOFError) as exc:
        raise BadInput('%s: not a gwt-check dump or a gwt-apply snapshot (%s)' % (path, exc))
    objs = data.get('objects', data) if isinstance(data, dict) else None
    if not isinstance(objs, dict):
        raise BadInput('%s: not a gwt-check dump or a gwt-apply snapshot' % path)
    places = objs.get('places', objs.get('Place'))
    events = objs.get('events', objs.get('Event')) or []
    if not isinstance(places, list) or not isinstance(events, list):
        raise BadInput('%s: no places list: is this a gwt-check dump or a gwt-apply snapshot?' % path)
    if any(not isinstance(o, dict) or 'handle' not in o for o in places + events):
        raise BadInput('%s: places and events must be objects with a handle' % path)
    return {p['handle']: p for p in places}, events


def load_overrides(path):
    """gramps_id -> (lat, long, note). A TSV; blank lines, `#` lines and a gramps_id header are skipped."""
    out = {}
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BadInput('%s: %s' % (path, exc))
    for n, line in enumerate(lines, 1):
        cells = line.split('\t')
        if not line.strip() or line.startswith('#') or cells[0] == 'gramps_id':
            continue
        try:
            lat, lon = float(cells[1]), float(cells[2])
        except (IndexError, ValueError):
            raise BadInput('%s line %d: want gramps_id, lat, long and an optional note, tab-separated' % (path, n))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise BadInput('%s line %d: lat/long out of range' % (path, n))
        out[cells[0].strip()] = (lat, lon, cells[3].strip() if len(cells) > 3 else '')
    return out


# ---- transport ---------------------------------------------------------------------------------------------------

class Http:
    """GET JSON with a User-Agent, a pace per host and an on-disk cache. Nominatim's policy: one request a second.
    A dropped connection, a timeout, 429 and 5xx are tried three times; anything else fails at once. A host that
    blocks us (403) or fails three questions in a row gets no more requests this run."""
    PACE = {'nominatim.openstreetmap.org': 1.1}
    DEFAULT_PACE = 0.5
    RETRY = (429, 500, 502, 503, 504)
    GIVE_UP = 3

    def __init__(self, cache_dir=None, contact='', clock=time.monotonic, sleep=time.sleep, opener=None):
        self.cache_dir, self.clock, self.sleep = cache_dir, clock, sleep
        self.ua = UA if not contact else UA[:-1] + '; ' + contact + ')'
        self.opener = opener or urllib.request.urlopen
        self.last = {}
        self.requests = 0
        self.ok = 0          # questions a provider answered
        self.failed = {}     # host -> questions it failed
        self.dead = {}       # host -> why it gets no more requests

    def _cache_path(self, url):
        return os.path.join(self.cache_dir, hashlib.sha256(url.encode()).hexdigest()[:32] + '.json')

    def get_json(self, url, params=None):
        if params:
            url += '?' + urllib.parse.urlencode(sorted(params.items()))
        if self.cache_dir:
            p = self._cache_path(url)
            if os.path.exists(p):
                try:
                    with open(p, encoding='utf-8') as f:
                        return json.load(f)
                except (OSError, ValueError):  # a damaged cache file: ask again, the answer replaces it
                    pass
        host = urllib.parse.urlsplit(url).hostname
        if host in self.dead:
            raise ProviderError(self.dead[host])
        for attempt in range(3):
            wait = self.last.get(host, -1e9) + self.PACE.get(host, self.DEFAULT_PACE) - self.clock()
            if wait > 0:
                self.sleep(wait)
            self.last[host] = self.clock()
            self.requests += 1
            try:
                req = urllib.request.Request(url, headers={'User-Agent': self.ua, 'Accept': 'application/json'})
                with self.opener(req, timeout=60) as r:
                    body = r.read()
                data = json.loads(body) if body.strip() else None  # 204, no content: no answer
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    self._fail(host, '%s answered 403: it blocks this client; check --contact' % host, final=True)
                if exc.code not in self.RETRY or attempt == 2:
                    self._fail(host, '%s answered %s' % (host, exc.code))
            except (OSError, http.client.HTTPException) as exc:  # URLError, a reset, a dropped connection, a timeout
                why = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                if isinstance(why, ssl.SSLError) or attempt == 2:  # a certificate does not fix itself in 5 s
                    self._fail(host, '%s: %s' % (host, why or type(exc).__name__))
            except ValueError:
                self._fail(host, '%s answered something that is not JSON' % host)
            self.sleep(5 * (attempt + 1))
        self.ok += 1
        self.failed[host] = 0
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
            p = self._cache_path(url)
            with open(p + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(p + '.tmp', p)
        return data


    def _fail(self, host, why, final=False):
        self.failed[host] = self.failed.get(host, 0) + 1
        if final or self.failed[host] >= self.GIVE_UP:
            self.dead[host] = why + '; no more requests to it this run'
            print('gwt-geocode: %s' % self.dead[host], file=sys.stderr)
        raise ProviderError(why)


def cand(lat, lon, kind, names, provider, confidence='', cc='', group=None, rank=0):
    """One provider answer. Answers with the same `group` (CartoCiudad's INE code) are one place; a lower `rank`
    is preferred among answers at the same distance (a town's point before its municipality's centroid)."""
    return {'lat': float(lat), 'lon': float(lon), 'kind': kind, 'names': [n for n in names if n],
            'provider': provider, 'confidence': confidence, 'cc': (cc or '').lower(), 'group': group, 'rank': rank}


# ---- providers ---------------------------------------------------------------------------------------------------

class Nominatim:
    name = 'nominatim'
    URL = 'https://nominatim.openstreetmap.org/search'

    def __init__(self, http):
        self.http = http

    def applies(self, ctx):
        return True

    def _search(self, **params):
        params.update(format='jsonv2', addressdetails='1', namedetails='1', limit='10')
        out = []
        for r in self.http.get_json(self.URL, params) or []:
            cat, typ, at = r.get('category'), r.get('type'), r.get('addresstype')
            if at in OSM_AREA and (cat == 'boundary' or typ in OSM_AREA):
                kind = 'area'
            elif (cat == 'place' and typ in OSM_TOWN) or (cat == 'boundary' and at in OSM_TOWN):
                kind = 'settlement'
            else:
                kind = 'spot'
            names = [r.get('name') or (r.get('display_name') or '').split(',')[0]]
            names += list((r.get('namedetails') or {}).values())
            out.append(cand(r['lat'], r['lon'], kind, names, self.name, '%.2f' % (r.get('importance') or 0),
                            (r.get('address') or {}).get('country_code')))
        return out

    def search(self, ctx):
        q = {'countrycodes': ctx['cc']} if ctx['cc'] else {}
        out = self._search(q=', '.join(ctx['path']), **q)
        if ctx['kind'] in ('spot', 'street') and ctx.get('town'):
            out += self._search(q='%s, %s' % (ctx['name'], ctx['town']), **q)
        return out

    def country_code(self, name):
        for r in self._search(q=name, featureType='country'):
            if r['kind'] == 'area' and r['cc'] and same_name(name, r['names']):
                return r['cc']
        return None


class Ban:
    """France's national address base: one municipality per commune, with its INSEE code."""
    name = 'ban'
    URL = 'https://api-adresse.data.gouv.fr/search/'

    def __init__(self, http):
        self.http = http

    def applies(self, ctx):
        return ctx['cc'] == 'fr' and ctx['kind'] == 'settlement'

    def search(self, ctx):
        data = self.http.get_json(self.URL, {'q': ctx['name'], 'type': 'municipality', 'limit': '5'}) or {}
        out = []
        for f in data.get('features') or []:
            pr, (lon, lat) = f.get('properties') or {}, f['geometry']['coordinates'][:2]
            out.append(cand(lat, lon, 'settlement', [pr.get('name'), pr.get('city')], self.name,
                            '%.2f' % (pr.get('score') or 0), 'fr'))
        return out


class CartoCiudad:
    """Spain's official geocoder, through gwt-fetch's client: towns and municipalities, and named spots."""
    name = 'cartociudad'

    def __init__(self, http):
        self.http = http

    def applies(self, ctx):
        return ctx['cc'] == 'es' and ctx['kind'] in ('settlement', 'spot')

    def json(self, url, params=None):  # the client shape gwt_fetch.geo expects
        return self.http.get_json(url, params)

    def search(self, ctx):
        try:
            from gwt_fetch import geo
        except ModuleNotFoundError:  # a clone
            sys.path.insert(0, os.path.join(HERE, '..', 'fetch'))
            from gwt_fetch import geo
        types = ['Municipio', 'poblacion'] if ctx['kind'] == 'settlement' else ['toponimo']
        kind = 'settlement' if ctx['kind'] == 'settlement' else 'spot'
        # a toponym's name is its address up to the comma: «Catedral de X, X»
        return [cand(r['lat'], r['lng'], kind, [r['name'].split(',')[0], r['municipality'] if kind == 'settlement'
                                                else ''], self.name, '', 'es', group=r['ine_code'] or None,
                     rank=0 if r['type'] != 'Municipio' else 1)
                for r in geo.geocode(self, ctx['name'], types, ctx.get('province'), limit=5) if r['lat'] and r['lng']]


class GeonamesDump:
    """A GeoNames dump (https://download.geonames.org/export/dump/: a country file such as IT.zip, or cities500.zip),
    read offline. Columns: geonameid, name, asciiname, alternatenames, lat, long, feature class, feature code,
    country code, ..."""
    name = 'geonames-dump'

    def __init__(self, paths):
        self.index = {}
        self.spots = []    # every spot row, for names longer than the place's own
        self.ending = {}   # lang -> last word of a spot's name, abbreviations written out -> rows
        for path in paths:
            for row in self._rows(path):
                c = row.split('\t')
                if len(c) < 9:
                    continue
                fclass, fcode = c[6], c[7]
                if fclass == 'P' or (fclass == 'A' and fcode in GEONAMES_TOWN_A):
                    kind = 'settlement'
                elif fclass == 'A':
                    kind = 'area'
                elif fclass == 'S':
                    kind = 'spot'
                else:
                    continue
                try:
                    r = cand(c[4], c[5], kind, [c[1], c[2]] + c[3].split(','), self.name, '', c[8])
                except ValueError:
                    continue
                r['code'] = fcode
                if kind == 'spot':
                    self.spots.append(r)
                for n in {rules.fold(x) for x in r['names']}:
                    self.index.setdefault(n, []).append(r)

    @staticmethod
    def _rows(path):
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as z:
                    member = next((n for n in z.namelist() if n.endswith('.txt') and n != 'readme.txt'), None)
                    if member is None:
                        raise BadInput('%s: no .txt dump inside' % path)
                    with z.open(member) as f:
                        yield from io.TextIOWrapper(f, encoding='utf-8')
            else:
                with open(path, encoding='utf-8') as f:
                    yield from f
        except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            raise BadInput('%s: %s' % (path, exc))

    def applies(self, ctx):
        return bool(self.index)

    def search(self, ctx):
        out = []
        for form in name_forms(ctx['name']):
            out += [r for r in self.index.get(form, []) if not ctx['cc'] or r['cc'] == ctx['cc']]
        if ctx['kind'] in ('spot', 'street'):  # a spot's gazetteer name may be longer: «Chiesa di San X» for «San X»
            last = (rules.dedication(ctx['name'], ctx['lang']).split() or [''])[-1]
            out += [r for r in self._endings(ctx['lang']).get(last, []) if r not in out
                    and (not ctx['cc'] or r['cc'] == ctx['cc'])]
        return out

    def _endings(self, lang):
        if lang not in self.ending:
            idx = self.ending[lang] = {}
            for r in self.spots:
                for n in {(rules.dedication(n, lang).split() or [''])[-1] for n in r['names'][:2]}:
                    idx.setdefault(n, []).append(r)
        return self.ending[lang]

    def country_code(self, name):
        for form in name_forms(name):
            for r in self.index.get(form, []):
                if r.get('code', '').startswith('PCL'):
                    return r['cc']
        return None


# ---- the geocoder ------------------------------------------------------------------------------------------------

class Geocoder:
    def __init__(self, places, events, providers, overrides=None, recheck=False, countries=None, lang=None):
        self.P, self.providers = places, providers
        self.overrides = overrides or {}
        self.recheck, self.lang = recheck, lang
        self.countries = {rules.fold(k): v.lower() for k, v in (countries or {}).items()}
        self.used = {e.get('place') for e in events if e.get('place') in places}
        self.memo = {}
        self.cc_memo = {}
        self.cc_errors = {}  # country name -> why a provider could not tell its code

    # place helpers
    def ptype(self, h):
        return text_of(self.P[h].get('place_type') or self.P[h].get('type'))

    def pname(self, h):
        return text_of(self.P[h].get('name')) or text_of(self.P[h].get('title'))

    def parent(self, h):
        refs = self.P[h].get('placeref_list') or []
        r = refs[0].get('ref') if refs else None
        return r if r in self.P else None

    def chain(self, h):
        out, seen = [], set()
        while h and h not in seen:
            seen.add(h)
            out.append(h)
            h = self.parent(h)
        return out

    def kind(self, h):
        t = self.ptype(h)
        if t in AREA:
            return 'area'
        if t in SETTLEMENT:
            return 'settlement'
        if t in LOCAL and self.town_above(h) is None:
            return 'settlement'
        return 'street' if t in STREET else 'spot'

    def tree_point(self, h):
        p = self.P[h]
        try:
            lat, lon = float(p.get('lat')), float(p.get('long'))
        except (TypeError, ValueError):
            return None
        return (lat, lon) if (lat, lon) != (0.0, 0.0) and -90 <= lat <= 90 and -180 <= lon <= 180 else None

    def unreadable_point(self, h):
        """The tree has a point, as gwt-check counts one (lat and long both filled in), but not in decimal degrees."""
        p = self.P[h]
        if not (str(p.get('lat') or '').strip() and str(p.get('long') or '').strip()):
            return False
        try:
            float(p.get('lat')), float(p.get('long'))
        except (TypeError, ValueError):
            return True
        return False

    def label(self, h):
        return ' > '.join(self.pname(x) for x in self.chain(h))

    def country_code(self, h):
        top = self.chain(h)[-1]
        if self.ptype(top) != 'Country':
            return None
        name = self.pname(top)
        key = rules.fold(name)
        if key in self.countries:
            return self.countries[key]
        if len(name.strip()) == 2 and name.strip().isalpha():
            return name.strip().lower()
        if key not in self.cc_memo:
            self.cc_memo[key] = None
            for p in self.providers:
                if hasattr(p, 'country_code'):
                    try:
                        cc = p.country_code(name)
                    except ProviderError as exc:
                        self.cc_errors.setdefault(key, []).append('%s: %s' % (p.name, exc))
                        cc = None
                    if cc:
                        self.cc_memo[key] = cc.lower()
                        break
        return self.cc_memo[key]

    def town_above(self, h):
        return next((a for a in self.chain(h)[1:] if self.kind(a) == 'settlement'), None)

    # resolution
    def point(self, h):
        """The resolution of place h: a dict with match, lat, lon and the rest of the report row (memoised)."""
        if h not in self.memo:
            self.memo[h] = {'match': 'unresolved', 'note': 'a loop in the place hierarchy'}  # a cycle stops here
            self.memo[h] = self._resolve(h)
        return self.memo[h]

    def _resolve(self, h):
        gid = self.P[h].get('gramps_id') or ''
        if gid in self.overrides:
            lat, lon, note = self.overrides[gid]
            return {'match': 'override', 'lat': lat, 'lon': lon, 'provider': 'overrides', 'note': note}
        if self.unreadable_point(h):  # 40°10'00"N: never overwrite what cannot be read
            return {'match': 'kept', 'note': 'already in the tree, not in decimal degrees: left as it is'}
        have = self.tree_point(h)
        if have and (not self.recheck or h not in self.used):
            return {'match': 'kept', 'lat': have[0], 'lon': have[1], 'note': 'already in the tree'}
        kind = self.kind(h)
        if kind == 'area' and h not in self.used:
            return {'match': 'unresolved', 'note': 'an area no event uses is not looked up'}
        cc = self.country_code(h)
        top = self.chain(h)[-1]
        if cc is None and self.ptype(top) == 'Country':  # never search worldwide: a namesake abroad would win
            return {'match': 'unresolved', 'note': '; '.join(
                ['the country %r has no known code: pass --country %s=CC' % ((self.pname(top),) * 2)]
                + self.cc_errors.get(rules.fold(self.pname(top)), []))}
        names = [self.pname(x) for x in self.chain(h)]
        town = self.town_above(h)
        ctx = {'name': self.pname(h), 'kind': kind, 'cc': cc, 'path': names,
               'lang': self.lang or LANG_OF.get(cc or '', 'en'),
               'town': self.pname(town) if town else '',
               'province': next((self.pname(a) for a in self.chain(h)[1:] if self.ptype(a) == 'Province'), None)}
        ref = self.point(town) if town else None
        ref = (ref['lat'], ref['lon']) if ref and 'lat' in ref else None
        if kind in ('spot', 'street') and not ref:
            return {'match': 'unresolved', 'note': 'no town above it with a point to check the distance against'}
        notes = []
        for p in self.providers:
            if not p.applies(ctx):
                continue
            try:
                found = p.search(ctx)
            except ProviderError as exc:
                notes.append('%s: %s' % (p.name, exc))
                continue
            got = self.choose(ctx, found, ref, notes)
            if got:
                d = km((got['lat'], got['lon']), ref) if ref else None
                return {'match': {'spot': 'own-point', 'street': 'own-point'}.get(kind, kind), 'lat': got['lat'],
                        'lon': got['lon'], 'provider': p.name, 'confidence': got['confidence'],
                        'distance_km': '' if d is None else '%.1f' % d, 'note': '; '.join(notes)}
        if ref:
            notes.insert(0, 'not found near %s' % ctx['town'])
            return {'match': 'town-centroid', 'lat': ref[0], 'lon': ref[1], 'distance_km': '0.0',
                    'note': '; '.join(notes)}
        return {'match': 'unresolved', 'note': '; '.join(['no provider found it'] + notes)}

    def choose(self, ctx, found, ref, notes):
        kind = ctx['kind']
        want = 'spot' if kind == 'street' else kind
        limit = STREET_KM if kind == 'street' else SPOT_KM if kind == 'spot' else LOCALITY_KM
        ok = []
        for c in found:
            if c['kind'] != want:
                continue
            if ctx['cc'] and c['cc'] and c['cc'] != ctx['cc']:
                notes.append('%s: %s is in another country (%s)' % (c['provider'], c['names'][0], c['cc']))
                continue
            if want == 'spot':
                if not any(same_ending(ctx['name'], n, ctx['lang'], ctx['town']) for n in c['names']):
                    continue
            elif not same_name(ctx['name'], c['names']):
                continue
            d = km((c['lat'], c['lon']), ref) if ref else None
            if d is not None and d > limit:
                notes.append('%s: %s is %.1f km from %s, over the %g km limit'
                             % (c['provider'], c['names'][0], d, ctx['town'], limit))
                continue
            ok.append((d or 0.0, c))
        if not ok:
            return None
        ok.sort(key=lambda dc: (dc[0], dc[1]['rank']))
        first = ok[0][1]
        if ref is None and any(km((c['lat'], c['lon']), (first['lat'], first['lon'])) > AMBIGUOUS_KM
                               and (c['group'] is None or c['group'] != first['group']) for _, c in ok[1:]):
            notes.append('%s: %d places of that name, %g+ km apart, and no town above to choose by'
                         % (ok[0][1]['provider'], len(ok), AMBIGUOUS_KM))
            return None
        return ok[0][1]

    def run(self):
        """(report rows, ops) for every place an event uses, then every container that already has a point."""
        rows, ops = [], []
        order = sorted(self.P, key=lambda h: (len(self.chain(h)), self.P[h].get('gramps_id') or h))
        for h in order:
            if h not in self.used:
                continue
            r = self.point(h)
            rows.append(self.row(h, r))
            if r['match'] in ('kept', 'unresolved') or 'lat' not in r:
                continue
            gid = self.P[h].get('gramps_id')
            have = self.tree_point(h)
            if have:  # only an override or --recheck gets here
                if r['match'] == 'town-centroid':  # a hand-placed spot is better than its town's point
                    rows[-1].update(lat='%.6f' % have[0], long='%.6f' % have[1], distance_km='')
                    self.add_note(rows[-1], "keeps the tree's point; no op")
                    continue
                moved = km(have, (r['lat'], r['lon']))
                if moved < (1e-6 if r['match'] == 'override' else MOVE_KM):
                    continue
                self.add_note(rows[-1], "moves the tree's point %.1f km, from %.6f, %.6f" % (moved, have[0], have[1]))
            if not gid:
                self.add_note(rows[-1], 'no Gramps ID, no op')
                continue
            ops.append({'op': 'update_place', 'key': 'geocode-%s' % gid, 'place': gid,
                        'set': {'lat': round(r['lat'], 6), 'long': round(r['lon'], 6)}})
        for h in order:
            if h not in self.used and (self.tree_point(h) or self.unreadable_point(h)):
                lat, lon = self.tree_point(h) or (None, None)
                r = {'match': 'container', 'note': 'no event uses it, yet it has a point: the map shows a marker'}
                rows.append(self.row(h, {**r, 'lat': lat, 'lon': lon} if lat is not None else r))
        return rows, ops

    @staticmethod
    def add_note(row, text):
        row['note'] = (row['note'] + '; ' if row['note'] else '') + text

    def row(self, h, r):
        return {'gramps_id': self.P[h].get('gramps_id') or '', 'place': self.label(h), 'type': self.ptype(h),
                'match': r['match'], 'provider': r.get('provider', ''), 'confidence': r.get('confidence', ''),
                'distance_km': r.get('distance_km', ''),
                'lat': '%.6f' % r['lat'] if 'lat' in r else '', 'long': '%.6f' % r['lon'] if 'lon' in r else '',
                'note': r.get('note', '')}


def build_providers(names, http, geonames):
    made = {'nominatim': lambda: Nominatim(http), 'ban': lambda: Ban(http), 'cartociudad': lambda: CartoCiudad(http),
            'geonames-dump': lambda: GeonamesDump(geonames)}
    return [made[n]() for n in names]


def main(argv=None, http=None):
    ap = argparse.ArgumentParser(prog='gwt-geocode', description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog='See https://geiserx.github.io/gramps-web-toolkit/geocode/')
    ap.add_argument('--snapshot', required=True, help='a gwt-check dump or gwt-apply snapshot (gzipped or not)')
    ap.add_argument('--out', required=True, help='write the update_place change list here (JSON Lines)')
    keep = ap.add_mutually_exclusive_group()
    keep.add_argument('--recheck', action='store_true',
                      help='look up places that already have a point too, and move them when the answer differs')
    keep.add_argument('--only-missing', action='store_true', help='leave places that have a point alone (the default)')
    ap.add_argument('--providers', default=','.join(PROVIDERS), help='comma-separated, in order (default: %(default)s)')
    ap.add_argument('--overrides', help='TSV of gramps_id, lat, long, note: points that always win')
    ap.add_argument('--geonames', action='append', default=[], metavar='FILE',
                    help='a GeoNames dump (.txt or .zip) for geonames-dump; repeat for several countries')
    ap.add_argument('--country', action='append', default=[], metavar='NAME=CC',
                    help='the ISO code of a country place, when no provider can tell it (France=fr)')
    ap.add_argument('--contact', default='', help="an e-mail address for Nominatim's User-Agent")
    ap.add_argument('--cache', default=os.path.join(os.path.expanduser('~'), '.cache', 'gwt-geocode'),
                    help='where provider answers are kept (default: %(default)s)')
    ap.add_argument('--no-cache', action='store_true', help='ask the providers again')
    ap.add_argument('--ruling', default='geocode', help='the ruling every op carries (default: %(default)s)')
    ap.add_argument('--lang', choices=rules.LOCALES, help="church abbreviations' language (default: by country)")
    a = ap.parse_args(argv)
    names = [n.strip() for n in a.providers.split(',') if n.strip()]
    bad = [n for n in names if n not in PROVIDERS]
    if bad or not names:
        ap.error('unknown provider %s; there are: %s' % (', '.join(bad) or '(none)', ', '.join(PROVIDERS)))
    if 'geonames-dump' in names and not a.geonames:
        names.remove('geonames-dump')
    countries = {}
    for item in a.country:
        k, _, v = item.partition('=')
        if len(v.strip()) != 2:
            ap.error('--country wants NAME=CC with a two-letter code, got %r' % item)
        countries[k.strip()] = v.strip()
    try:
        places, events = load_snapshot(a.snapshot)
        overrides = load_overrides(a.overrides) if a.overrides else {}
        try:  # before any request: a long run must not end on a typo in --out
            out = open(a.out, 'w', encoding='utf-8')
        except OSError as exc:
            raise BadInput('--out: %s' % exc)
        http = http or Http(None if a.no_cache else a.cache, a.contact)
        g = Geocoder(places, events, build_providers(names, http, a.geonames), overrides, a.recheck,
                     countries, a.lang)
        rows, ops = g.run()
    except BadInput as exc:
        print('gwt-geocode: %s' % exc, file=sys.stderr)
        return 2
    with out as f:
        for op in ops:
            f.write(json.dumps({**op, 'ruling': a.ruling}, ensure_ascii=False) + '\n')
    print('\t'.join(COLUMNS))
    for r in rows:
        print('\t'.join(str(r[c]).replace('\t', ' ') for c in COLUMNS))
    count = {}
    for r in rows:
        count[r['match']] = count.get(r['match'], 0) + 1
    print('gwt-geocode: %d ops to %s; %s' % (len(ops), a.out, ', '.join('%s %d' % kv for kv in sorted(count.items()))),
          file=sys.stderr)
    if getattr(http, 'requests', 0) and not getattr(http, 'ok', 1):
        print('gwt-geocode: no provider answered a single request; the notes say why', file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
