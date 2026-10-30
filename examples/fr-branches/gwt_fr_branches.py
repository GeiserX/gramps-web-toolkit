#!/usr/bin/env python3
"""gwt-fr-branches: read-only. From a `gwt-check dump` and `gwt-leads --format json` output, rank research leads
for every French branch of a tree and say which record to look for and where. Never writes to the tree, never
calls a site. Private use: the dump holds private people; do not publish the output."""
import csv, json, re, sys, collections, urllib.parse

DEPT = {'Indre-et-Loire': ('AD37', 37), 'Loir-et-Cher': ('AD41', 41), 'Orne': ('AD61', 61), 'Sarthe': ('AD72', 72),
        'Nord': ('AD59', 59), 'Ville de Paris': ('Archives de Paris', 75), 'Seine (hoy Seine-Saint-Denis)': ('Archives de Paris / AD93', 93)}

import os
PRIORITY = {x.strip().lower() for x in os.environ.get('GWT_FR_PRIORITY', '').split(',') if x.strip()}  # surnames to boost, kept out of the code

# Portals seen live on 30/10/2026 (search results of each archive's own site). Others: not verified, left empty.
PORTAL = {37: 'https://archives.touraine.fr/search/results?keyword={q}',
          59: 'https://archivesdepartementales.lenord.fr/search/results?keyword={q}',
          41: 'http://archives.culture41.fr/archives/recherche/etatcivil/',
          61: 'https://archives.orne.fr/etat-civil',  # online to 1902, tables decennales to 1942 (portal text, 30/10/2026)
          72: 'https://archives.sarthe.fr/archives-en-ligne/registres-paroissiaux-etat-civil/chercher-dans-letat-civil',
          75: 'https://archives.paris.fr/archives-numerisees/etat-civil-de-paris/etat-civil-a-partir-de-1860/actes-detat-civil'}  # births 1860-1924, marriages 1860-1950, deaths 1860-1986

# AD37 commune codes read from the portal's own commune search (30/10/2026, first 40 results per commune, so a
# missing series only means "not seen"). 6NUM8 = registres d'état civil, 6NUM2 = tables décennales (as the portal's
# notices show). Cote shape: 6NUM8/<code>/<n>.
AD37 = {'Marray': {'8': '149', '2': '149'}, 'Limeray': {'8': '131'}, 'Chançay': {'8': '052'},
        'Saint-Laurent-en-Gâtines': {'2': '224', '8': '224'}, 'Monnaie': {'8': '153'}, 'La Ferrière': {'8': '106'},
        'Vernou-sur-Brenne': {'2': '270'}}
# AD41 states online: births and marriages 1793-1925, deaths 1793 to the 1950s-60s, tables décennales 1793-1952.

def ledger_keys(path):
    if not path: return set()
    import os
    keys = set()
    for line in open(path, encoding='utf-8'):
        c = line.rstrip('\n').split('\t')
        if c and c[0] != 'person_key': keys.add((c[0].lower(), (c[2] if len(c) > 2 else '')))
    return keys

def load(path):
    d = json.load(open(path)); o = d['objects']
    return {k: {x['handle']: x for x in o[k]} for k in ('people', 'families', 'events', 'places')}

def chain(places, h):
    out = []
    while h and h in places and len(out) < 8:
        p = places[h]; out.append(p['name']['value'] if isinstance(p.get('name'), dict) else p.get('title', ''))
        refs = p.get('placeref_list') or []
        h = refs[0]['ref'] if refs else None
    return out

def year(ev):
    dv = (ev.get('date') or {}).get('dateval') or []
    return dv[2] if len(dv) > 2 and dv[2] else None

def main(dump, leads, out_csv, out_md, ledger=None):
    done = {k[0] for k in ledger_keys(ledger)}
    T = load(dump); P, F, E, PL = T['people'], T['families'], T['events'], T['places']
    def surname(p):
        sl = p['primary_name'].get('surname_list') or []
        return (sl[0]['surname'] if sl else '').strip()
    info = {}
    for h, p in P.items():
        places, yrs = [], []
        for r in p.get('event_ref_list') or []:
            e = E.get(r['ref'])
            if not e: continue
            if e.get('place'): places.append(chain(PL, e['place']))
            if year(e): yrs.append(year(e))
        info[p['gramps_id']] = dict(h=h, name=(p['primary_name'].get('first_name') or '') + ' ' + surname(p),
                                    sn=surname(p), places=places, yrs=yrs,
                                    fr=any('Francia' in c for c in places))
    rows = []
    for line in open(leads):
        l = json.loads(line); i = info.get(l['person'])
        if not i or not i['fr']: continue
        if l['person'].lower() in done: continue   # already searched, see the ledger
        commune = next((c[0] for c in i['places'] if 'Francia' in c), '')
        ch = next((c for c in i['places'] if 'Francia' in c), [])
        dept = next((DEPT[x] for x in ch if x in DEPT), ('', ''))
        by = min(i['yrs']) if i['yrs'] else None
        if by is None and isinstance(l.get('facts', {}).get('by'), str):
            m = re.search(r'\d{4}', l['facts']['by']); by = int(m.group()) if m else None
        score = l['priority']
        if l['kind'] == 'branch-end': score += 6
        if commune and '(?)' not in commune and 'precisar' not in commune: score += 4
        if by and 1793 <= by <= 1922: score += 3   # civil registers online with decennial tables
        if by and by < 1793: score += 1            # parish registers (BMS), harder
        if not by or by > 1925: score -= 8         # may be living: no outside search
        if dept[0]: score += 2
        if i['sn'].lower() in PRIORITY: score += 3   # ancestors named in the standing goal
        if not by or by > 1925: what = 'NO buscar fuera: posible vivo o sin fecha'
        elif by >= 1793: what = f'acta civil + table décennale (naissance/mariage/décès) cerca de {by}' + (f' en {commune}' if commune else '')
        else: what = f'registre paroissial BMS cerca de {by}' + (f' en {commune}' if commune else '')
        fam = ''
        if l['kind'] == 'branch-end': fam = 'subir: padres del acta de nacimiento/matrimonio; después hermanos del mismo registro (±8 años)'
        link = ''
        if by and by <= 1900 and i['sn']:
            fn = i['name'].replace(i['sn'], '').strip().split(' ')[0]
            link = 'https://www.geneanet.org/fonds/individus/?' + urllib.parse.urlencode({'nom': i['sn'], 'prenom': fn, 'go': 1})
        serie = ''
        cm = commune.split(' (')[0]
        if dept[1] == 37 and cm in AD37:
            serie = ' ; '.join(f'6NUM{k}/{v}/' for k, v in sorted(AD37[cm].items()))
        if dept[1] == 41: serie = 'sin cota por comuna: formulario AD41 (nac./mat. hasta 1925, defunciones hasta ~1950-60, tablas 1793-1952)'
        url = PORTAL.get(dept[1], '').format(q=urllib.parse.quote(commune.split(' (')[0])) if dept[1] else ''
        rows.append(dict(score=score, person=l['person'], nombre=i['name'].strip(), apellido=i['sn'], tipo=l['kind'],
                         hueco=l['what'], comuna=commune, depto=dept[1], archivo=dept[0], nacio_aprox=by or '',
                         buscar=what, serie_ad=serie, portal_archivo_verificado=url, ampliar=fam, enlace_busqueda_geneanet_patron_sin_verificar=link))
    rows.sort(key=lambda r: -r['score'])
    with open(out_csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    br = collections.defaultdict(list)
    for r in rows: br[r['apellido']].append(r)
    with open(out_md, 'w') as f:
        f.write('# Rama francesa: pistas rankeadas (privado, solo lectura)\n\n')
        f.write(f'{len(rows)} huecos en {len(br)} apellidos. Puntuación = prioridad de gwt-leads + fin de rama + comuna segura + años 1793-1922 (acta civil en línea) + archivo departamental conocido. Nada de posibles vivos.\n\n')
        f.write('## Por rama (apellido)\n\n| Apellido | Huecos | Fines de rama | Mejor pista |\n|---|---|---|---|\n')
        for sn, rs in sorted(br.items(), key=lambda kv: -max(r['score'] for r in kv[1])):
            top = rs[0]
            f.write(f"| {sn} | {len(rs)} | {sum(1 for r in rs if r['tipo']=='branch-end')} | {top['nombre']} ({top['person']}): {top['buscar']} |\n")
        f.write('\n## Top 25 pistas\n\n')
        for r in rows[:25]:
            f.write(f"- [{r['score']}] {r['nombre']} ({r['person']}) - {r['hueco']}. {r['buscar']}. {r['ampliar']}\n")
    return len(rows), len(br)

if __name__ == '__main__':
    print(main(*sys.argv[1:6]))
