#!/usr/bin/env python3
"""gwt-rank: keep only the strong matches from a page of record-search results.

A search by name returns thousands of rows. This reads the visible text of a FamilySearch-style
results page (`gwt-rank PAGE.txt --facts "by=1860 bp=Tours dy=1933" --name "Antonin Lechat"`) and prints
the rows that agree with what the tree already knows, best first. It never says a row IS the person:
a strong row is a lead for the verifier and the judge, a page with no strong row is "no strong lead on
this page", not an absence.

  gwt-rank PAGE.txt --name "Given Surname" [--facts "by=1949 bp=Tours dy=2005 dp=Murcia"] [--spouse NAME]
           [--top 20] [--min 3] [--format tsv|json]

Exit 0 with the strong rows, 1 when the page parsed but none is strong, 2 when the page holds no
records (not a results page, or not loaded): never read that as a negative.
"""
import argparse
import difflib
import json
import re
import sys
import unicodedata

ROLES = {'principal', 'father', 'mother', 'spouse', 'husband', 'wife', 'son', 'daughter', 'child',
         'groom', 'bride', 'deceased', 'self', 'head', 'other'}
EVENT = re.compile(r'^(Birth|Baptism|Christening|Death|Burial|Marriage|Residence|Census|Immigration|Emigration)\b[^\n]*?\s(\d{4})?', re.I)
YEAR = re.compile(r'\b(1[0-9]{3}|20[0-2][0-9])\b')


def norm(text):
    t = unicodedata.normalize('NFKD', text or '')
    t = ''.join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9 ]+', ' ', t).split()


GENERIC = {'san', 'santa', 'santo', 'saint', 'sainte', 'st', 'ste', 'la', 'le', 'les', 'el', 'los', 'las', 'new', 'de', 'del', 'du',
           'des', 'sur', 'sous', 'en', 'y', 'and', 'of', 'the', 'villa', 'puerto', 'port', 'mont', 'monte', 'val', 'ville'}


def place_match(want, have):
    # every significant token of one place must appear in the other; a shared generic token (San, Saint, La, New) never counts
    a = [w for w in want if w not in GENERIC] or want
    b = [w for w in have if w not in GENERIC] or have
    return all(w in have for w in a) or all(w in want for w in b)


def records(text):
    """Split page text into records: a name line followed by a role line starts one."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    starts = [i for i in range(len(lines) - 1) if lines[i + 1].lower() in ROLES and lines[i].lower() not in ROLES]
    out = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        body = lines[i + 2:end]
        rec = {'name': lines[i], 'role': lines[i + 1], 'lines': body, 'by': None, 'dy': None, 'places': [], 'bp': '', 'dp': '', 'spouses': ''}
        rec['b' + 'y'] = None
        j = 0
        while j < len(body):
            l = body[j]
            kind = re.match(r'^(Birth|Baptism|Christening)\b', l, re.I) and 'b' or re.match(r'^(Death|Burial)\b', l, re.I) and 'd'
            if kind:
                # the year is on the event line, or on the next one when the date sits on its own line
                k = j if YEAR.search(l) else (j + 1 if j + 1 < len(body) and YEAR.search(body[j + 1]) else None)
                if k is not None and rec[kind + 'y'] is None:
                    rec[kind + 'y'] = int(YEAR.search(body[k]).group(1))
                    if k + 1 < len(body) and not re.match(r'^(Birth|Baptism|Christening|Death|Burial|Marriage|Parents|Spouse|More)\b', body[k + 1], re.I):
                        rec['places'].append(body[k + 1])
                        rec[kind + 'p'] = body[k + 1]
                    j = k
            m = re.match(r'^Spouses?\s*:?\s+(.+)$', l, re.I)
            if m:
                rec['spouses'] = m.group(1)
            j += 1
        rec['text'] = ' '.join(body)
        out.append(rec)
    return out


def parse_facts(s):
    f = {}
    for part in (s or '').split():
        if '=' in part:
            k, v = part.split('=', 1)
            f[k] = v.replace('_', ' ')
    return f


def score(rec, name, facts, spouse='', window=3):
    given, sur = norm(name)[:-1], norm(name)[-1:]
    rn = norm(rec['name'])
    s, why = 0, []
    if not (rec['role'].lower() == 'principal'):
        return 0, ['not the principal of the record']
    if sur and (sur[0] in rn or any(''.join(rn[i:j]) == sur[0] for i in range(len(rn)) for j in range(i + 2, min(len(rn), i + 3) + 1))):   # whole token, or 'le chat' = 'lechat'
        s += 1; why.append('surname')
    else:
        return 0, ['surname differs']
    if not given:
        pass
    elif given[0] in rn:
        s += 1; why.append('given name')
    elif any(len(w) >= 4 and difflib.SequenceMatcher(None, given[0], w).ratio() >= 0.8 for w in rn):
        why.append('given name variant')   # Antonin ~ Antoine: a hint, worth no point
    else:
        return 0, ['given name differs']
    for key, y in (('by', rec['by']), ('dy', rec['dy'])):
        want = facts.get(key)
        if want and y:
            if abs(int(want) - y) <= window:
                s += 2 if int(want) == y else 1; why.append('%s %s~%s' % (key, y, want))
            else:
                return 0, ['%s %s contradicts %s: vetoed' % (key, y, want)]
    for key in ('bp', 'dp'):   # each place belongs to its own event: birth place to birth, death place to death
        want = facts.get(key)
        have = norm(rec.get(key, ''))
        if want and have and place_match(norm(want), have):
            s += 1; why.append('%s %s' % (key, want))
    if spouse and rec.get('spouses') and all(w in norm(rec['spouses']) for w in norm(spouse)[:2]):
        s += 2; why.append('spouse')   # only a line that says Spouse(s), never Parents or free text
    return s, why


def rank(text, name, facts, spouse='', top=20, min_score=3):
    recs = records(text)
    if not recs or not any(r['by'] or r['dy'] or r['places'] for r in recs):
        return None   # nothing parsed, or no row carries a readable event: not a negative
    scored = []
    for r in recs[:top]:
        sc, why = score(r, name, facts, spouse)
        if sc >= min_score:
            scored.append({'score': sc, 'name': r['name'], 'by': r['by'], 'dy': r['dy'],
                           'places': r['places'][:2], 'why': why})
    scored.sort(key=lambda x: -x['score'])
    return scored


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-rank', description=__doc__.split('\n')[0])
    p.add_argument('page')
    p.add_argument('--name', required=True)
    p.add_argument('--facts', default='')
    p.add_argument('--spouse', default='')
    p.add_argument('--top', type=int, default=20)
    p.add_argument('--min', type=int, default=3)
    p.add_argument('--format', choices=('tsv', 'json'), default='tsv')
    a = p.parse_args(argv)
    try:
        text = open(a.page, encoding='utf-8').read()
    except OSError as exc:
        print('gwt-rank: %s' % exc, file=sys.stderr)
        return 2
    res = rank(text, a.name, parse_facts(a.facts), a.spouse, a.top, a.min)
    if res is None:
        print('gwt-rank: no records on this page: not a results page or not loaded', file=sys.stderr)
        return 2
    for r in res:
        if a.format == 'json':
            print(json.dumps(r, ensure_ascii=False))
        else:
            print('\t'.join([str(r['score']), r['name'], str(r['by'] or ''), str(r['dy'] or ''),
                             '; '.join(r['places']), ', '.join(r['why'])]))
    return 0 if res else 1


if __name__ == '__main__':
    sys.exit(main())
