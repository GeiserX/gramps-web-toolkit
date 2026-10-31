#!/usr/bin/env python3
"""gwt_insee: INSEE's "fichier des personnes décédées" (the French deaths file, data.gouv.fr), read offline for gwt-read.

INSEE publishes one fixed-width text file per year (and per month and quarter for the current year) at
https://www.data.gouv.fr/datasets/fichier-des-personnes-decedees/ . Each line is one death registered in France,
in the layout the dataset page gives ("Dessin d'enregistrement"), positions counted from 1:

    1-80     nom et prénoms     NOM*PRENOMS, often closed by a slash: DUPONT*JEAN PIERRE/
    81       sexe               1 masculin, 2 féminin
    82-89    date de naissance  AAAAMMJJ, 0000 / 00 / 00 for an unknown year, month or day
    90-94    code du lieu de naissance (code officiel géographique; 99xxx is a country)
    95-124   commune de naissance en clair
    125-154  pays de naissance en clair (blank for a birth in France)
    155-162  date de décès      AAAAMMJJ, as above
    163-167  code du lieu de décès
    168-176  numéro d'acte de décès
    177-     filler; the dataset page warns that some lines carry meaningless characters at the end

The reader never touches the network: the files are a bulk download the user makes once. Each line whose surname
matches one of the --surname filters becomes one gwt-rank candidate: the principal of a death record, with a birth
event (year and commune as written) and a death event (year and the death place code). The full dates, the codes,
the country and the act number stay as extra fields gwt-rank does not score. Dates are kept as written: INSEE's
files hold calendar dates that never existed (29 February of a common year).

Every line is checked, matched or not. A line whose columns do not line up is an error naming the file and line,
never skipped: a date that is not eight digits, a sex that is not 1 or 2, a line too short to hold the death date.
Some values break INSEE's own rules with the columns still in place: a month 13, a day 60, an 80-character name with no
`*` (one line each in the 1991, 1996 and 2012 files). Those are what INSEE wrote, so the line is kept: the year is the
date's first four digits, a name with no `*` is the surname whole, and the candidate lists the field under
`malformed`. Each file's count of such lines goes into the envelope and on stderr. Blank lines are skipped.

Encoding: the files are ASCII in practice (names and communes are upper case, without accents), and the columns
count characters, not bytes. A few lines, mostly foreign birthplaces, are not ASCII, and the years differ. In the 2005
and 2010 files they are the DOS code page CP850: 0xA5 is Ñ (LA CAÑADA), 0xC7 Ã (SÃO), 0xE0 Ó (MATARÓ), 0xF8 the degree
sign after an arrondissement number (PARIS 12°). The 1999 file mixes CP850 lines with Latin-1 ones (0xF1 ñ, 0xD1 Ñ,
0xB0 °, 0xE9 é). The 2021 and 2022 files carry a few UTF-8 lines (°, ², é), counted in characters. So a line is read
as UTF-8 when it is valid UTF-8 (plain ASCII is). Otherwise it is read both as CP850 and as Latin-1, and the reading
with more accented letters of the French, Spanish and Portuguese alphabets (and °) wins; a tie goes to CP850. Such a
candidate carries `"encoding": "cp850"` or `"encoding": "latin-1"`: the odd characters are our guess, so the
verifier reads them against the file. Standard library only.
"""
import hashlib, os, re

import gwt_reader

SOURCE = 'insee'
NAME, SEX, BIRTH_DATE, BIRTH_CODE, BIRTH_TOWN, BIRTH_COUNTRY, DEATH_DATE, DEATH_CODE, ACT = (
    (0, 80), (80, 81), (81, 89), (89, 94), (94, 124), (124, 154), (154, 162), (162, 167), (167, 176))
MIN_LENGTH = DEATH_DATE[1]      # a line must reach the end of the death date; the code and act may be cut short
SEXES = {'1': 'M', '2': 'F'}
_DATE = re.compile(r'^(\d{4})(\d{2})(\d{2})$')


class LayoutError(ValueError):
    """A line that is not in INSEE's layout: the run stops, nothing is written."""


# accented letters INSEE's birthplaces use, and the degree sign; CP850's box and line characters, ±, Ð, Þ, ø are not
PLAUSIBLE = set('ÀÂÄÇÈÉÊËÎÏÔÖÙÛÜÑÃÕÁÍÓàâäçèéêëîïôöùûüÿñãõáíóú°')


def _plausible(text):
    return sum(c in PLAUSIBLE for c in text)


def decode(raw):
    """(text, encoding) of one line without its line end: UTF-8 (ASCII is UTF-8), else CP850 or Latin-1, whichever
    reads as more accented letters; CP850 on a tie."""
    raw = raw.rstrip(b'\r\n')
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        pass
    cp850, latin1 = raw.decode('cp850'), raw.decode('latin-1')
    if _plausible(latin1) > _plausible(cp850):
        return latin1, 'latin-1'
    return cp850, 'cp850'


def _field(line, span):
    return line[span[0]:span[1]].strip()


def check_date(value):
    """(year, odd) of an AAAAMMJJ date: the year is None when INSEE wrote 0000, odd is True for a month over 12 or a
    day over 31. Raises ValueError when it is not eight digits (the columns are shifted)."""
    m = _DATE.match(value)
    if not m:
        raise ValueError('date %r is not AAAAMMJJ' % value)
    y, mo, d = (int(x) for x in m.groups())
    return y or None, mo > 12 or d > 31


def parse_line(line):
    """One line as a dict of its fields, or LayoutError (with the reason) when it is not in the layout."""
    if len(line) < MIN_LENGTH:
        raise LayoutError('line is %d characters, the layout needs at least %d' % (len(line), MIN_LENGTH))
    name = _field(line, NAME)
    sex = line[SEX[0]:SEX[1]]
    if sex not in SEXES:
        raise LayoutError('sex %r is not 1 or 2' % sex)
    surname, star, given = name.partition('*')
    rec = {'surname': surname.rstrip('/').strip(), 'given': given.rstrip('/').strip(), 'sex': SEXES[sex],
           'malformed': [] if star else ['name']}
    for key, span in (('birth_date', BIRTH_DATE), ('death_date', DEATH_DATE)):
        rec[key] = line[span[0]:span[1]]
        try:
            rec[key[:-5] + '_year'], odd = check_date(rec[key])
        except ValueError as e:
            raise LayoutError('%s: %s' % (key.replace('_', ' '), e))
        if odd:
            rec['malformed'].append(key)
    for key, span in (('birth_place_code', BIRTH_CODE), ('birth_commune', BIRTH_TOWN), ('birth_country', BIRTH_COUNTRY),
                      ('death_place_code', DEATH_CODE), ('act', ACT)):
        rec[key] = _field(line, span)
    return rec


def fold(text):
    """Words of a name with case and accents folded, the same rule gwt-read's keys use."""
    return gwt_reader.norm_words(text)


def surname_matches(surname, wanted):
    """True when one wanted surname stands as whole words inside the line's surname: ORTEGA matches ORTEGA and
    ORTEGA SANZ, never ORTEGAS. `wanted` holds folded word lists."""
    have = fold(surname)
    for w in wanted:
        n = len(w)
        if n and any(have[i:i + n] == w for i in range(len(have) - n + 1)):
            return True
    return False


def candidate(rec, path, lineno, encoding):
    """One parsed line as a gwt-rank candidate record."""
    place = rec['birth_commune']
    if rec['birth_country']:
        place = '%s, %s' % (place, rec['birth_country']) if place else rec['birth_country']
    by, dy = rec['birth_year'], rec['death_year']
    c = {'source': SOURCE,
         # the death's date, place and act number are how INSEE itself names a death (its opposition file uses them)
         'id': '%s-%s-%s' % (rec['death_date'], rec['death_place_code'], rec['act']),
         'name': ' '.join(x for x in (rec['given'], rec['surname']) if x),
         'role': 'Principal', 'principal': True,
         'given': rec['given'] or None, 'surnames': [rec['surname']] if rec['surname'] else None,
         'sex': rec['sex'],
         'events': {'birth': {'year': by, 'year_to': by, 'place': place},
                    'death': {'year': dy, 'year_to': dy, 'place': rec['death_place_code']}},
         'relatives': [],
         'birth_date': rec['birth_date'], 'birth_place_code': rec['birth_place_code'],
         'birth_commune': rec['birth_commune'], 'birth_country': rec['birth_country'],
         'death_date': rec['death_date'], 'death_place_code': rec['death_place_code'], 'act': rec['act'],
         'file': os.path.basename(path), 'line': lineno}
    if encoding != 'utf-8':
        c['encoding'] = encoding
    if rec['malformed']:
        c['malformed'] = rec['malformed']
    return c


def read_files(paths, surnames):
    """(candidates, per-file summaries) for every line of every file whose surname matches. Raises LayoutError with
    the file and line of the first line out of layout, and OSError for a file that cannot be read."""
    wanted = [fold(s) for s in surnames if fold(s)]
    cands, files = [], []
    for path in paths:
        h, lines, matched, odd = hashlib.sha256(), 0, 0, 0
        with open(path, 'rb') as f:
            for lineno, raw in enumerate(f, 1):
                h.update(raw)
                line, enc = decode(raw)
                if not line.strip():
                    continue
                lines += 1
                try:
                    rec = parse_line(line)
                except LayoutError as e:
                    raise LayoutError('%s line %d: %s' % (path, lineno, e))
                odd += bool(rec['malformed'])
                if surname_matches(rec['surname'], wanted):
                    matched += 1
                    cands.append(candidate(rec, path, lineno, enc))
        files.append({'file': os.path.basename(path), 'sha256': h.hexdigest(), 'lines': lines, 'matched': matched,
                      'malformed': odd})
    return cands, files
