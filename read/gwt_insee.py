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
files hold calendar dates that never existed (29 February of a common year), so only the digits, the month (00 to
12) and the day (00 to 31) are checked.

Every line is checked, matched or not: a line with a malformed date, a sex that is not 1 or 2, no `*` in the name or
too short to hold the death date is an error naming the file and line, never skipped. Blank lines are skipped.

Encoding: the files are ASCII in practice (names and communes are upper case, without accents). A few lines, mostly
foreign birthplaces, carry bytes from the DOS code page CP850: in samples of the 2005 and 2010 files 0xA5 is Ñ (LA
CAÑADA), 0xC7 is Ã (SÃO, GUIMARÃES), 0xE0 is Ó (MATARÓ), 0xF8 the degree sign after an arrondissement number
(PARIS 12°); read as Latin-1 they make no sense. We found no UTF-8 in them. So a line is read as UTF-8 (plain ASCII
is both) and, when that fails, as CP850, which maps every byte and never fails; such a candidate carries
`"encoding": "cp850"`, so the lane knows the odd characters are INSEE's code page, read our way. Standard library only.
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


def decode(raw):
    """(text, encoding) of one line without its line end: UTF-8 (ASCII is UTF-8), else CP850."""
    raw = raw.rstrip(b'\r\n')
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return raw.decode('cp850'), 'cp850'


def _field(line, span):
    return line[span[0]:span[1]].strip()


def check_date(value):
    """The year of an AAAAMMJJ date, or None when INSEE wrote 0000; raises ValueError when it is not a date."""
    m = _DATE.match(value)
    if not m:
        raise ValueError('date %r is not AAAAMMJJ' % value)
    y, mo, d = (int(x) for x in m.groups())
    if mo > 12 or d > 31:
        raise ValueError('date %r has month %02d, day %02d' % (value, mo, d))
    return y or None


def parse_line(line):
    """One line as a dict of its fields, or LayoutError (with the reason) when it is not in the layout."""
    if len(line) < MIN_LENGTH:
        raise LayoutError('line is %d characters, the layout needs at least %d' % (len(line), MIN_LENGTH))
    name = _field(line, NAME)
    if '*' not in name:
        raise LayoutError('name %r has no "*" between the surname and the given names' % name)
    sex = line[SEX[0]:SEX[1]]
    if sex not in SEXES:
        raise LayoutError('sex %r is not 1 or 2' % sex)
    surname, _, given = name.partition('*')
    rec = {'surname': surname.strip(), 'given': given.rstrip('/').strip(), 'sex': SEXES[sex]}
    for key, span in (('birth_date', BIRTH_DATE), ('death_date', DEATH_DATE)):
        rec[key] = line[span[0]:span[1]]
        try:
            rec[key[:-5] + '_year'] = check_date(rec[key])
        except ValueError as e:
            raise LayoutError('%s: %s' % (key.replace('_', ' '), e))
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
    return c


def read_files(paths, surnames):
    """(candidates, per-file summaries) for every line of every file whose surname matches. Raises LayoutError with
    the file and line of the first line out of layout, and OSError for a file that cannot be read."""
    wanted = [fold(s) for s in surnames if fold(s)]
    cands, files = [], []
    for path in paths:
        h, lines, matched = hashlib.sha256(), 0, 0
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
                if surname_matches(rec['surname'], wanted):
                    matched += 1
                    cands.append(candidate(rec, path, lineno, enc))
        files.append({'file': os.path.basename(path), 'sha256': h.hexdigest(), 'lines': lines, 'matched': matched})
    return cands, files
