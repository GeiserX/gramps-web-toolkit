#!/usr/bin/env python3
"""gwt_boe: the BOE and Gaceta de Madrid search (boe.es/buscar/gazeta.php), for gwt-read.

The official open-data API (boe.es/datosabiertos) answers by date and by law, never by a person's name, and its
daily summaries start in 1961. The site's own search covers the Gaceta de Madrid and the BOE back to 1661 and takes
a phrase in the text of the acts. A phrase in quotes is read as one name; without quotes the site matches the
words anywhere, so the reader always quotes it.

A hit is a mention of a name in an official act (a pardon, an appointment, a pension, a call for places). It says
nothing about which person it is: a common name hits many namesakes. Each row becomes a candidate of role
`mention` with the act's title in the note, never a principal, so gwt-rank can weigh it and nobody takes it for a
record. Only the first page is read; `total` says how many rows the site found. Standard library only.
"""
import html, re, shutil, subprocess, unicodedata, urllib.parse

HOST = 'https://www.boe.es'
SOURCE = 'boe'
# A public service whose robots.txt disallows the search: one request every 6 s at best, never faster, even after
# hours without a hit (floor = start); after a hit one every 120 s.
PACE = {'start': 6.0, 'floor': 6.0, 'ceil': 120.0}
ACCEPT = 'text/html'
LANGUAGE = 'es-ES,es;q=0.9'
PAGE_HITS = 50

_ITEM = re.compile(r'<li class="resultado-busqueda">(.*?)</li>\s*(?=<li class="resultado-busqueda">|</ul>\s*</div>)', re.S)
_TOTAL = re.compile(r'Resultados\s+(\d+)\s+a\s+(\d+)\s+de\s+([\d.]+)')
_NEXT = re.compile(r'href="(gazeta\.php\?accion=Mas[^"]*)"[^>]*>\s*<span class="pagSig">')
_REF = re.compile(r'\(Referencia ([A-Z0-9-]+)\)')


def text(fragment):
    t = html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))
    return re.sub(r'\s+', ' ', t).strip()


def _iso(d, end):
    """A year or an ISO date as the site's own date field wants it (yyyy-mm-dd); None when empty or not a date."""
    d = str(d or '').strip()
    if re.fullmatch(r'\d{4}', d):
        return d + ('-12-31' if end else '-01-01')
    return d if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) else None


def search_url(phrase, date_from=None, date_to=None):
    """The URL of one search: the phrase in quotes, in the text of the acts, optionally between two publication
    dates (a year or yyyy-mm-dd; the site's historical search ends in 1959)."""
    q = [('campo[4]', 'DOC'), ('dato[4]', '"%s"' % phrase.replace('"', '')), ('operador[4]', 'and'),
         ('page_hits', str(PAGE_HITS)), ('accion', 'Buscar')]
    lo, hi = _iso(date_from, False), _iso(date_to, True)
    if lo or hi:
        q += [('campo[8]', 'FPU'), ('dato[8][0]', lo or '1661-01-01'), ('dato[8][1]', hi or '1959-12-31')]
    return '%s/buscar/gazeta.php?%s' % (HOST, urllib.parse.urlencode(q, quote_via=urllib.parse.quote))


def is_results(page):
    """True only for a results page, with or without rows. The form alone, an error or a block page is not an answer."""
    p = page or ''
    return bool(_TOTAL.search(p) or 'No se han encontrado' in p or 'no ha encontrado' in p)


def total(page):
    m = _TOTAL.search(page or '')
    return int(m.group(3).replace('.', '')) if m else 0


def _date(s):
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})', s or '')
    return ('%s-%s-%s' % (m.group(3), m.group(2), m.group(1)), int(m.group(3))) if m else ('', None)


def pdf_url(href, date):
    """The act's PDF at /gazeta/dias/yyyy/mm/dd/pdfs/<file>. The results link to the same file under /datos/pdfs/,
    which robots.txt disallows ("No duplicar los pdfs"); both serve the same bytes. The link as given when the row
    has no date."""
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date or ''):
        return '%s/gazeta/dias/%s/pdfs/%s' % (HOST, date.replace('-', '/'), href.rsplit('/', 1)[-1])
    return HOST + href


def rows(page):
    """[{title, ref, date, year, paper, dept, pdf}] for every result of the page."""
    out = []
    for item in _ITEM.findall(page or ''):
        title = re.search(r'<p class="linea-pub">(.*?)</p>', item, re.S)
        dd = [text(x) for x in re.findall(r'<dd>(.*?)</dd>', item, re.S)]
        dt = [text(x) for x in re.findall(r'<dt>(.*?)</dt>', item, re.S)]
        ref = _REF.search(item)
        pdf = re.search(r'href="([^"]+\.pdf)"', item)
        date, year = _date(dd[0] if dd else '')
        out.append({'title': text(title.group(1)) if title else '', 'ref': ref.group(1) if ref else '',
                    'date': date, 'year': year, 'paper': dt[0].rstrip(':') if dt else '',
                    'dept': dd[1] if len(dd) > 1 else '', 'pdf': pdf_url(pdf.group(1), date) if pdf else ''})
    return out


def next_url(page):
    """The URL of the next page of the same search, or None on the last one."""
    m = _NEXT.search(page or '')
    return '%s/buscar/%s' % (HOST, html.unescape(m.group(1))) if m else None


def _fold(t):
    return re.sub(r'\s+', ' ', ''.join(c for c in unicodedata.normalize('NFD', t or '') if unicodedata.category(c) != 'Mn').lower())


def candidates(page, phrase, near=(), born=None):
    """Each result as a gwt-rank candidate of role `mention`: the name searched, the year of the act, the act's title."""
    out = []
    for r in rows(page):
        ev = {'mention': {'year': r['year'], 'year_to': r['year'], 'place': ''}} if r['year'] else {}
        titled = _fold(phrase) in _fold(r['title'])
        young = bool(born and r['year'] and (r['year'] < born + 14 or r['year'] > born + 100))
        hits = [w for w in near if _fold(w) in _fold(r['title'] + ' ' + r['dept'])]
        out.append({'source': SOURCE, 'id': r['ref'] or r['title'][:40], 'role': 'mention', 'principal': False,
                    'phonetic': False, 'url': r['pdf'] or HOST + '/buscar/gazeta.php', 'date': r['date'],
                    'in_title': titled, 'near': hits, 'implausible': young,
                    'note': '%s (%s, %s)%s%s: %s' % (r['paper'], r['dept'], r['date'], ' [name in title]' if titled else '',
                                                    ' [near: %s]' % ', '.join(hits) if hits else '', r['title']) if not young else
                    '%s (%s, %s) [IMPLAUSIBLE: published %d, a person born %d was under 14 or over 100]: %s' % (
                        r['paper'], r['dept'], r['date'], r['year'], born, r['title']),
                    'locality': '', 'name': phrase, 'given': None, 'surnames': None, 'events': ev, 'relatives': []})
    return out


def verdict(c):
    """One word per act, so a long list reads at a glance. `excluded` when the act cannot be the person (published
    before the birth, other parents, a longer name), `consistent` when the parents match the tree's, `open` otherwise.
    An act is never dropped and no word is proof: a document still decides."""
    if c.get('implausible') or c.get('same_parents') is False or c.get('preceded_by'):
        return 'excluded'
    if c.get('same_parents'):
        return 'consistent'
    return 'open'


def pdf_text(data):
    """The text of a PDF with `pdftotext` when it is installed, else None. Nothing is installed or run besides it."""
    exe = shutil.which('pdftotext')
    if not exe or not data:
        return None
    try:
        r = subprocess.run([exe, '-', '-'], input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode('utf-8', 'replace') if r.returncode == 0 else None


def context(text, phrase, width=140):
    """The words around the first place the whole name stands in the text of an act, accents and case ignored,
    a hyphen at a line end joined; None when the name is not there whole (a column break can split it)."""
    flat = re.sub(r'-\s*\n\s*|\xad\s*', '', text or '')
    flat = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', flat))
    f = ''.join(c for c in unicodedata.normalize('NFD', flat) if unicodedata.category(c) != 'Mn')
    i = f.lower().find(_fold(phrase))
    if i < 0 or len(f) != len(flat):
        return None
    return flat[max(0, i - width):i + len(phrase) + width].strip()


def preceded_by(text, phrase):
    """The word that stands right before the first whole-name hit when it starts with a capital letter, else None.
    "Juan Ana Zorvalo" holds "Ana Zorvalo" and is another man."""
    flat = re.sub(r'-\s*\n\s*|\xad\s*', '', text or '')
    flat = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', flat))
    f = ''.join(c for c in unicodedata.normalize('NFD', flat) if unicodedata.category(c) != 'Mn')
    i = f.lower().find(_fold(phrase))
    if i < 0 or len(f) != len(flat):
        return None
    m = re.search(r'([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+) $', flat[max(0, i - 40):i])
    return m.group(1) if m and m.group(1).lower() not in ('don', 'doña', 'dona', 'de', 'del') else None


_PARENTS = re.compile(r'^[.,;]?\s*(?:hij[oa]\s+)?de\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)\s+(?:y|é|e)\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)')


def parents_after(text, phrase):
    """The parents' first names when the act writes them right after the name ("Ana Zorvalo Quemada, de Juan y
    Antonia", as the lists of the draft do), as (father, mother), else None."""
    flat = re.sub(r'-\s*\n\s*|\xad\s*', '', text or '')
    flat = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', flat))
    f = ''.join(c for c in unicodedata.normalize('NFD', flat) if unicodedata.category(c) != 'Mn')
    i = f.lower().find(_fold(phrase))
    if i < 0 or len(f) != len(flat):
        return None
    m = _PARENTS.match(flat[i + len(phrase):i + len(phrase) + 60])
    return (m.group(1), m.group(2)) if m else None


def same_parents(found, wanted):
    """True when the parents' first names the act gives are the ones the tree holds ("José y Josefa"), accents and case
    ignored; False when they differ; None when either side is missing."""
    if not found or not wanted:
        return None
    w = [x.strip() for x in re.split(r'\s+(?:y|e|é|and)\s+|/', wanted) if x.strip()]
    return len(w) == 2 and [_fold(x) for x in w] == [_fold(x) for x in found]
