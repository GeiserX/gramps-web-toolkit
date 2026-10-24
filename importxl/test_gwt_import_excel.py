import gzip, json, os, subprocess, sys, tempfile, unittest, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_import_excel as X

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gwt-import-excel')


def make_xlsx(path, sheets, inline=False):
    """Tiny synthetic workbook: sheets = {name: [[cell, ...], ...]} (all text)."""
    shared, ss = [], {}

    def esc(s):
        return s.replace('&', '&amp;').replace('<', '&lt;')
    wb = '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    z = zipfile.ZipFile(path, 'w')
    for i, (name, rows) in enumerate(sheets.items(), 1):
        wb += '<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (name, i, i)
        rels += '<Relationship Id="rId%d" Target="worksheets/sheet%d.xml"/>' % (i, i)
        body = ''
        for r, row in enumerate(rows, 1):
            body += '<row r="%d">' % r
            for c, v in enumerate(row):
                if v == '':
                    continue
                ref = chr(65 + c) + str(r)
                if inline:
                    body += '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, esc(v))
                else:
                    ss.setdefault(v, len(ss)); shared.append(v) if ss[v] == len(shared) else None
                    body += '<c r="%s" t="s"><v>%d</v></c>' % (ref, ss[v])
            body += '</row>'
        z.writestr('xl/worksheets/sheet%d.xml' % i, '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>' % body)
    z.writestr('xl/workbook.xml', wb + '</sheets></workbook>')
    z.writestr('xl/_rels/workbook.xml.rels', rels + '</Relationships>')
    if not inline:
        z.writestr('xl/sharedStrings.xml', '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">%s</sst>' % ''.join('<si><t>%s</t></si>' % esc(s) for s in shared))
    z.close()


def make_native(path):
    # serial 1901-03-14 = 1901-01-01 is 1462? computed below with datetime
    import datetime
    serial = (datetime.date(1901, 3, 14) - datetime.date(1899, 12, 30)).days
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    hdr = ''.join('<c r="%s1" t="inlineStr"><is><t>%s</t></is></c>' % (chr(65 + i), h) for i, h in enumerate(HEAD))
    name = lambda r, g: '<c r="A%d" t="inlineStr"><is><t>%s</t></is></c><c r="B%d" t="inlineStr"><is><t>X</t></is></c>' % (r, g, r)
    rows = [
        '<row r="2">%s<c r="D2" s="1"><v>%d</v></c></row>' % (name(2, 'A'), serial),
        '<row r="3">%s<c r="D3"><v>1901</v></c></row>' % name(3, 'B'),
        '<row r="4">%s<c r="D4" s="2"><v>%d</v></c></row>' % (name(4, 'C'), serial),
        '<row r="5">%s<c r="D5" t="e"><v>#REF!</v></c></row>' % name(5, 'D'),
        '<row r="6">%s<c r="D6"><f>1900+50</f><v>1950</v></c></row>' % name(6, 'E'),
        '<row r="8">%s<c r="D8" s="3"><v>1</v></c></row>' % name(8, 'G'),
        '<row r="9">%s<c r="D9" s="4"><v>1</v></c></row>' % name(9, 'H'),
        '<row r="10">%s<c r="D10" s="5"><v>1</v></c></row>' % name(10, 'I'),
        '<row r="11">%s<c r="D11" s="7"><v>1</v></c></row>' % name(11, 'J'),
        '<row r="12">%s<c r="D12" s="6"><v>%d</v></c></row>' % (name(12, 'K'), serial),
        '<row r="7">%s<c r="D7" s="1"><f>DATE(1901,3,14)</f><v>%d</v></c></row>' % (name(7, 'F'), serial),
    ]
    z = zipfile.ZipFile(path, 'w')
    z.writestr('xl/workbook.xml', '<workbook %s xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="People" sheetId="1" r:id="rId1"/></sheets></workbook>' % ns)
    z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
    z.writestr('xl/styles.xml', '<styleSheet %s><numFmts count="4"><numFmt numFmtId="164" formatCode="d/m/yyyy"/><numFmt numFmtId="165" formatCode="h:mm AM/PM"/><numFmt numFmtId="166" formatCode="[h]:mm:ss"/><numFmt numFmtId="167" formatCode="mmm yyyy"/></numFmts><cellXfs count="8"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="46"/><xf numFmtId="165"/><xf numFmtId="166"/><xf numFmtId="167"/><xf numFmtId="21"/></cellXfs></styleSheet>' % ns)
    z.writestr('xl/worksheets/sheet1.xml', '<worksheet %s><sheetData><row r="1">%s</row>%s</sheetData></worksheet>' % (ns, hdr, ''.join(rows)))
    z.close()


HEAD = ['Given', 'Surname', 'Sex', 'Born', 'Birth place', 'Died', 'Death place', 'Source', 'Page', 'Why apart', 'Birth place id']
MAP = {'ruling': 'R-1', 'sheets': [{'sheet': 'People', 'header_row': 1, 'columns': {
    'first_name': 'Given', 'surname': 'Surname', 'gender': 'Sex', 'birth_date': 'Born', 'birth_place': 'Birth place',
    'death_date': 'Died', 'death_place': 'Death place', 'source': 'Source', 'page': 'Page',
    'namesake_reason': 'Why apart', 'birth_place_id': 'Birth place id'}}]}
HERE = os.path.dirname(os.path.abspath(__file__))
YEAR = 2026


def row(first, sur, sex='', born='', bplace='', died='', dplace='', src='Book', page='p', why='', bplace_id=''):
    return [first, sur, sex, born, bplace, died, dplace, src, page, why, bplace_id]


def run(rows, tree=None, mapping=MAP, inline=False, batch='b', **kw):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'b.xlsx')
        make_xlsx(p, {'People': [HEAD] + rows}, inline)
        return X.import_rows(X.read_workbook(p), mapping, tree, batch=batch, this_year=YEAR, **kw)


def tree_of(people=(), places=()):
    """What read_tree returns, for (gramps id, first, surname) and (gramps id, place name)."""
    full, lo, pl = {}, {}, {}
    for pid, first, sur in people:
        full.setdefault((X.fold(first), X.fold(sur)), []).append(pid)
        lo.setdefault(X.loose(first, sur), []).append(pid)
    for pid, name in places:
        pl.setdefault(X.fold(name), []).append(pid)
    return {'full': full, 'loose': lo}, pl


def of(ops, kind):
    return [o for o in ops if o['op'] == kind]


class T(unittest.TestCase):
    def test_reads_shared_and_inline_strings(self):
        for inline in (False, True):
            ops, rep = run([row('Ana', 'Perez', 'F', '1901', 'Madrid', '', '', 'Parish book', 'f 3')], inline=inline)
            self.assertEqual(rep['people'], 1)
            self.assertEqual(ops[0]['first_name'], 'Ana')

    def test_dates_are_never_guessed(self):
        self.assertEqual(X.parse_date('1901-03-14')[0], '1901-03-14')
        self.assertEqual(X.parse_date('14/03/1901')[0], '1901-03-14')
        self.assertEqual(X.parse_date('14-03-1901')[0], '1901-03-14')
        self.assertEqual(X.parse_date('c. 1860')[0], 'abt 1860')
        self.assertEqual(X.parse_date('Mar 1901')[0], '1901-03')
        self.assertEqual(X.parse_date('?'), (None, None))
        self.assertEqual(X.parse_date('31/02/19xx')[0], None)
        self.assertEqual(X.parse_date('13/13/1901'), (None, 'impossible date'))
        self.assertEqual(X.parse_date('14/03-1901'), (None, 'unreadable date'))     # mixed separators
        self.assertEqual(X.parse_date('spring of the war'), (None, 'unreadable date'))

    def test_spanish_and_french_month_names_whole_words_only(self):
        for text, want in (('14 enero 1901', '1901-01-14'), ('3 abril 1901', '1901-04-03'), ('5 agosto 1901', '1901-08-05'),
                           ('9 diciembre 1901', '1901-12-09'), ('14 de marzo de 1901', '1901-03-14'), ('Setiembre 1901', '1901-09'),
                           ('1er janvier 1901', '1901-01-01'), ('3 février 1901', '1901-02-03'), ('15 août 1901', '1901-08-15'),
                           ('2 juillet 1901', '1901-07-02'), ('7 décembre 1901', '1901-12-07'), ('mai 1901', '1901-05'),
                           ('hacia 1860', 'abt 1860'), ('antes de 1900', 'bef 1900'), ('après 1900', 'aft 1900'),
                           ('14 March 1901', '1901-03-14'), ('Sept. 1901', '1901-09')):
            self.assertEqual(X.parse_date(text), (want, None), text)
        for text in ('marble 1901', 'decimo 1901', 'junk 1901', 'enerox 1901'):
            self.assertEqual(X.parse_date(text), (None, 'unreadable date'), text)
        self.assertEqual(X.parse_date('31 abril 1901'), (None, 'impossible date'))

    def test_ambiguous_day_and_month_are_reported(self):
        ops, rep = run([row('Ana', 'Perez', 'F', '03/04/1901'), row('Luis', 'Gil', 'M', '14/04/1901'), row('Eva', 'Sanz', 'F', '04/04/1901')])
        self.assertEqual(rep['day_first_assumed'], [{'row': 'People!2', 'event': 'Birth', 'text': '03/04/1901', 'read_as': '1901-04-03'}])

    def test_unreadable_date_is_kept_as_text_not_as_a_date(self):
        ops, rep = run([row('Ana', 'Perez', 'F', 'spring of the war')])
        ev = of(ops, 'add_event')[0]
        self.assertNotIn('date', ev)
        self.assertEqual(ev['description'], 'Date as written: spring of the war')
        self.assertEqual(rep['date_problems'][0]['reason'], 'unreadable date')

    def test_question_mark_makes_no_event(self):
        ops, rep = run([row('Ana', 'Perez', 'F', '?', '', '?', '')])
        self.assertEqual(of(ops, 'add_event'), [])
        self.assertEqual(rep['date_problems'], [])

    def test_row_without_source_gets_a_note_and_is_reported(self):
        ops, rep = run([row('Ana', 'Perez', 'F', '1901', src='', page='')])
        self.assertEqual(rep['no_source'], ['People!2'])
        self.assertTrue(any(o['op'] == 'add_note' and o['text'].startswith('[no source]') for o in ops))
        self.assertFalse(of(ops, 'add_citation'))

    def test_source_becomes_citation_on_person_and_events(self):
        ops, _ = run([row('Ana', 'Perez', 'F', '1901', 'Madrid', '1970', 'Madrid', 'Parish book', 'f 3'),
                      row('Luis', 'Gil', 'M', '1899', 'Madrid', '', '', 'Parish book', '')])
        self.assertEqual(len(of(ops, 'add_source')), 1)   # one source, two citations
        cit = of(ops, 'add_citation')
        self.assertEqual(len(cit), 2)
        self.assertEqual(len(cit[0]['attach']), 3)
        self.assertEqual(cit[1]['page'], '(no page in workbook, row People!3)')   # an empty page never shares a citation

    def test_gender_values_in_spanish_and_french(self):
        sexes = ['V', 'varón', 'Masculino', 'H', 'homme', 'M', 'F', 'mujer', 'Hembra', 'femme', 'Féminin', '', '?', 'xx']
        ops, rep = run([row('P%d' % i, 'Q', s, '1800') for i, s in enumerate(sexes)])
        self.assertEqual([o['gender'] for o in of(ops, 'add_person')], list('MMMMMMFFFFFUUU'))
        self.assertEqual(rep['gender_unread'], [{'row': 'People!15', 'text': 'xx'}])

    def test_maybe_living_people_are_held(self):
        rows = [row('Ana', 'Perez', 'F', '1950'), row('Luis', 'Gil', 'M', '1950', died='2001'),
                row('Eva', 'Sanz', 'F', '1925'), row('Rosa', 'Mora', 'F', '1940', died='?'), row('Pio', 'Vila', 'M', '')]
        ops, rep = run(rows)
        self.assertEqual([h['name'] for h in rep['living_held']], ['Ana Perez', 'Rosa Mora'])
        self.assertEqual(sorted(o['first_name'] for o in of(ops, 'add_person')), ['Eva', 'Luis', 'Pio'])
        ops, rep = run(rows, allow_living=True)
        self.assertEqual((rep['living_held'], rep['people']), ([], 5))

    def test_keys_are_namespaced_by_batch_sheet_row_and_name(self):
        same = [row('Luis', 'Gil', 'M', '1899')]
        a, _ = run(same, batch='cousin-a')
        b, _ = run(same, batch='archive-b')
        self.assertFalse({o['key'] for o in a} & {o['key'] for o in b})
        moved, _ = run([row('Ana', 'Perez', 'F', '1901')] + same, batch='cousin-a')   # a row inserted above
        self.assertFalse({o['key'] for o in a} & {o['key'] for o in moved if o['op'] != 'add_source'})
        self.assertTrue(of(a, 'add_person')[0]['key'].startswith('xl-cousin-a-people-r2-luis-gil-'))
        ops, _ = run([row('Ana', 'Perez', 'F', '1901', 'Madrid', '1970', 'Madrid', 'B', 'p'), row('Ana', 'Perez', 'F', '1901', src='B', page='q')])
        keys = [o['key'] for o in ops]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(o['ruling'] == 'R-1' for o in ops))

    def test_namesakes_are_reported_and_only_a_reason_lets_them_through(self):
        tree = tree_of([('I0007', 'Ana', 'Pérez'), ('I0008', 'Ana María', 'Perez Gil')])
        ops, rep = run([row('Ana', 'Perez', 'F', '1901'),                                  # namesake of I0007, loose of I0008
                        row('ana', 'PEREZ', 'F', '1950', died='2000', why='born 1950, married in Lyon'),
                        row('Luis', 'Gil', 'M', '1800', why='no reason needed')], tree)
        p1, p2, p3 = of(ops, 'add_person')
        self.assertNotIn('allow_namesake', p1)                   # no reason: left for gwt-apply preflight to refuse
        self.assertEqual(rep['namesakes'][0], {'row': 'People!2', 'name': 'Ana Perez', 'tree_ids': ['I0007', 'I0008'], 'rows': [], 'reason': None})
        self.assertEqual(p2['allow_namesake'], True)
        self.assertEqual(p2['namesake_reason'], 'born 1950, married in Lyon')
        self.assertEqual(p2['namesake_of'], ['I0007', 'I0008', 'key:' + p1['key']])
        self.assertNotIn('allow_namesake', p3)
        self.assertEqual(rep['namesake_reason_unused'], [{'row': 'People!4', 'name': 'Luis Gil'}])

    def test_places_come_from_ids_or_one_tree_match_never_new(self):
        tree = tree_of(places=[('P0001', 'Springfield'), ('P0002', 'San Juan'), ('P0003', 'San Juan')])
        ops, rep = run([row('Ana', 'Perez', 'F', '1901', 'springfield', '1970', 'Otherton'),
                        row('Luis', 'Gil', 'M', '1899', 'San Juan', '1960', 'San  Juan'),
                        row('Eva', 'Sanz', 'F', '1890', 'whatever', bplace_id='P0003'),
                        row('Pio', 'Vila', 'M', '1890', 'whatever', bplace_id='P0099'),
                        row('Rosa', 'Mora', 'F', '1890', bplace_id='a handle')], tree)
        self.assertEqual(of(ops, 'add_place'), [])
        ev = {o['key'].split('-r', 1)[1]: o for o in of(ops, 'add_event')}
        place = lambda r, e: next(v for k, v in ev.items() if k.startswith('%d-' % r) and k.endswith(e))
        self.assertEqual(place(2, 'birth')['place'], 'P0001')
        self.assertNotIn('place', place(2, 'death'))
        self.assertEqual(place(2, 'death')['description'], 'Place as written: Otherton')
        self.assertNotIn('place', place(3, 'birth'))
        self.assertEqual(place(4, 'birth')['place'], 'P0003')
        self.assertNotIn('place', place(5, 'birth'))
        self.assertEqual(rep['places_unmatched'], [{'row': 'People!2', 'event': 'Death', 'text': 'Otherton'}])
        self.assertEqual([a['tree_ids'] for a in rep['places_ambiguous']], [['P0002', 'P0003']] * 2)
        self.assertEqual([u['id'] for u in rep['place_ids_unknown']], ['P0099', 'a handle'])
        ops, rep = run([row('Ana', 'Perez', 'F', '1901', 'Springfield', bplace_id='P0042')])   # no tree: the id is trusted to gwt-apply
        self.assertEqual(of(ops, 'add_event')[0]['place'], 'P0042')

    def test_incomplete_name_is_skipped_with_reason(self):
        ops, rep = run([row('Ana', '', 'F', '1901')])
        self.assertEqual(ops, [])
        self.assertEqual(rep['skipped'][0]['reason'], 'name incomplete')

    def test_missing_or_repeated_column_names_the_column(self):
        bad = json.loads(json.dumps(MAP)); bad['sheets'][0]['columns']['source'] = 'Fuente'
        with self.assertRaises(X.MappingError) as c:
            run([row('Ana', 'Perez', 'F', '1901')], mapping=bad)
        self.assertIn('Fuente', str(c.exception))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'b.xlsx')
            make_xlsx(p, {'People': [HEAD[:-1] + ['Born'], row('Ana', 'Perez', 'F', '1901')]})
            with self.assertRaises(X.MappingError) as c:
                X.import_rows(X.read_workbook(p), MAP, None)
            self.assertIn("'Born'", str(c.exception))

    def test_output_passes_gwt_apply_validation(self):
        sys.path.insert(0, os.path.join(HERE, '..', 'apply'))
        try:
            from gwt_apply.ops import validate
        except ImportError:
            self.skipTest('gwt-apply not importable (needs requests)')
        ops, _ = run([row('Ana', 'Perez', 'F', '1901', 'Madrid', '1970', 'Madrid', 'Parish book', 'f 3'),
                      row('Luis', 'Gil', 'M', 'spring', src='', page=''),
                      row('Luis', 'Gil', 'M', '1890', why='another baptism book')], tree_of([('I0001', 'Luis', 'Gil')]))
        self.assertTrue(of(ops, 'add_person')[2]['allow_namesake'])
        self.assertEqual(validate(ops), [])

    def test_impossible_calendar_dates_are_refused(self):
        for bad in ('31/02/1901', '99 March 1901', '2026-02-29', '1901-04-31', '0/3/1901', '29 Feb 1900', '31 Jun 1901', '1901-02-00', '00 March 1901', '0000', '1901-00', '1901-00-10', '00/03/1901', '0000-05-01', 'c. 0000'):
            self.assertEqual(X.parse_date(bad)[0], None, bad)
            self.assertEqual(X.parse_date(bad)[1], 'impossible date', bad)
        for good in ('29/02/1904', '2024-02-29', '29 Feb 1904', '1901-02'):
            self.assertIsNotNone(X.parse_date(good)[0], good)
        self.assertEqual(X.parse_date('circa 1901')[0], 'abt 1901')
        self.assertEqual(X.parse_date('ca. 1901')[0], 'abt 1901')

    def test_sources_that_slug_alike_keep_their_own_keys(self):
        ops, _ = run([row('Ana', 'Perez', 'F', '1901', src='Register A-B', page='f1'),
                      row('Luis', 'Gil', 'M', '1899', src='Register A B', page='f2')])
        srcs = of(ops, 'add_source')
        self.assertEqual(sorted(s['title'] for s in srcs), ['Register A B', 'Register A-B'])
        self.assertEqual(len({o['source'] for o in of(ops, 'add_citation')}), 2)

    def test_native_excel_dates_formulas_and_errors(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'n.xlsx')
            make_native(p)
            meta = {}
            book = X.read_workbook(p, meta)
            rows = book['People']
            self.assertEqual(rows[1][3], '1901-03-14')          # date-styled serial 1901-03-14 -> ISO, not a year
            self.assertEqual(rows[2][3], '1901')                # plain number stays what was typed, never read as a serial
            self.assertEqual(rows[3][3], '1901-03-14')          # custom d/m/yyyy style counts as a date
            self.assertEqual(rows[4][3], '')                    # #REF! error is empty, flagged
            self.assertEqual(rows[5][3], '1950')                # formula: cached value, flagged
            self.assertEqual(meta[('People', 5, 3)], {'error'})
            self.assertEqual(meta[('People', 6, 3)], {'formula'})
            for r in (7, 8, 9, 10):                     # time of day and durations are never dates
                self.assertEqual(rows[r][3], '1')
                self.assertEqual(meta[('People', r + 1, 3)], {'time'})
            self.assertEqual(rows[11][3], '1901-03-14')  # "mmm yyyy" is a date format
            m = {'ruling': 'R-1', 'sheets': [{'sheet': 'People', 'columns': {'first_name': 'Given', 'surname': 'Surname', 'birth_date': 'Born'}}]}
            ops, rep = X.import_rows(book, m, None, meta, this_year=YEAR)
            self.assertEqual(rep['time_cells'], ['People!%d:Born' % r for r in (8, 9, 10, 11)])
            self.assertFalse(any(o.get('date') == '1900-01-01' for o in ops))
            self.assertEqual(rep['excel_dates'], 4)
            self.assertEqual(rep['formula_cells'], ['People!6:Born', 'People!7:Born'])   # a date-styled formula is still a formula
            self.assertEqual(meta[('People', 7, 3)], {'formula', 'date'})
            self.assertEqual(rep['error_cells'], ['People!5:Born'])
            by = {o['person']: o.get('date') for o in of(ops, 'add_event')}
            self.assertEqual(sorted(v for v in by.values() if v), ['1901', '1901-03-14', '1901-03-14', '1901-03-14', '1901-03-14'])
            self.assertEqual([h['born'] for h in rep['living_held']], ['1950'])

    def test_time_cell_that_looks_like_a_year_is_not_a_year(self):
        meta = {('People', 2, 3): {'time'}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'b.xlsx')
            make_xlsx(p, {'People': [HEAD, row('Ana', 'Perez', 'F', '1901')]})
            ops, rep = X.import_rows(X.read_workbook(p), MAP, None, meta, this_year=YEAR)
        ev = of(ops, 'add_event')
        self.assertTrue(ev and all('date' not in e for e in ev))
        self.assertEqual(ev[0]['description'], 'Time/duration value as written: 1901')
        self.assertEqual(rep['date_problems'][0]['reason'], 'time or duration format, not a date')

    def test_time_and_duration_formats_are_not_dates(self):
        for code in ('[h]:mm:ss', 'h:mm', 'mm:ss', 'h:mm AM/PM', '[mm]:ss', '[s]'):
            self.assertEqual(X.classify_format(code), 'time', code)
        for code in ('d/m/yyyy', 'mmm yyyy', 'yyyy', 'mm/dd/yy', '[$-409]d-mmm-yy', 'dd/mm/yyyy h:mm'):
            self.assertEqual(X.classify_format(code), 'date', code)
        for code in ('General', '0.00', '[Red]0', '@'):
            self.assertIsNone(X.classify_format(code), code)
        self.assertFalse({18, 19, 20, 21, 32, 33, 45, 46, 47} & X.BUILTIN_DATE_FMT)

    def test_rows_out_of_order_in_the_sheet_xml_keep_their_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'n.xlsx')
            make_native(p)       # the fixture lists row 12 before row 7
            self.assertEqual(X.read_workbook(p)['People'][6][0], 'F')

    def test_serials_out_of_range_or_1900_bug_are_not_dates(self):
        self.assertIsNone(X.serial_to_iso('60'))
        self.assertIsNone(X.serial_to_iso('1.5'))
        self.assertIsNone(X.serial_to_iso('0'))      # 1900 system has no day 0
        self.assertEqual(X.serial_to_iso('1'), '1900-01-01')
        self.assertEqual(X.serial_to_iso('61'), '1900-03-01')
        self.assertEqual(X.serial_to_iso('0', True), '1904-01-01')
        self.assertEqual(X.serial_to_iso('1461', True), '1908-01-01')

    def test_cells_and_rows_without_r_take_the_next_position(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'b.xlsx')
            raw_sheet(p, '<row><c t="inlineStr"><is><t>a</t></is></c><c t="inlineStr"><is><t>b</t></is></c></row>'
                         '<row r="4"><c r="C4" t="inlineStr"><is><t>c</t></is></c><c t="inlineStr"><is><t>d</t></is></c></row>'
                         '<row><c t="inlineStr"><is><t>e</t></is></c></row>')
            self.assertEqual(X.read_workbook(p)['S'], [['a', 'b'], (), (), ['', '', 'c', 'd'], ['e']])

    def test_absurd_rows_and_zip_bombs_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'b.xlsx')
            raw_sheet(p, '<row r="1000000000"><c r="A1000000000"><v>1</v></c></row>')
            with self.assertRaisesRegex(X.MappingError, 'row numbered 1000000000'):
                X.read_workbook(p)
            raw_sheet(p, '<row r="1"><c r="ZZZZ1"><v>1</v></c></row>')
            with self.assertRaisesRegex(X.MappingError, 'past column'):
                X.read_workbook(p)
            raw_sheet(p, '<row r="1"><c r="A1"><v>%s</v></c></row>' % ('0' * 5000))
            old = X.MAX_MEMBER
            X.MAX_MEMBER = 4000
            try:
                with self.assertRaisesRegex(X.MappingError, 'unpacks to more than'):
                    X.read_workbook(p)
            finally:
                X.MAX_MEMBER = old

    def test_read_tree_takes_a_check_dump_or_an_apply_snapshot(self):
        person = {'gramps_id': 'I0001', 'handle': 'h1', 'primary_name': {'first_name': 'Ana María', 'surname_list': [{'surname': 'Pérez'}, {'surname': 'Gil'}]}}
        place = {'gramps_id': 'P0001', 'handle': 'h2', 'name': {'value': 'Springfield'}}
        with tempfile.TemporaryDirectory() as d:
            for name, objs in (('dump.json.gz', {'people': [person], 'places': [place]}),
                               ('snap.json', {'Person': [person], 'Place': [place]})):
                p = os.path.join(d, name)
                with (gzip.open if name.endswith('.gz') else open)(p, 'wt', encoding='utf-8') as f:
                    json.dump({'objects': objs}, f)
                people, places = X.read_tree(p)
                self.assertEqual(people['full'], {('ana maria', 'perez gil'): ['I0001']})
                self.assertEqual(people['loose'], {('ana', 'perez'): ['I0001']})
                self.assertEqual(places, {'springfield': ['P0001']})

    def test_cli_writes_jsonl_and_never_touches_a_tree(self):
        with tempfile.TemporaryDirectory() as d:
            b, m, o = (os.path.join(d, n) for n in ('b.xlsx', 'm.json', 'o.jsonl'))
            make_xlsx(b, {'People': [HEAD, row('Ana', 'Perez', 'F', '1901', 'Madrid')]})
            with open(m, 'w') as f:
                json.dump(MAP, f)
            cli = lambda *extra: subprocess.run([sys.executable, CLI, b, '--mapping', m, '--out', o] + list(extra), capture_output=True, text=True)
            r = cli('--batch', 'cousin-a')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('people 1', r.stdout)
            with open(o) as f:
                self.assertEqual(json.loads(f.readline())['op'], 'add_person')
            r = cli('--batch', 'cousin-a')
            self.assertEqual(r.returncode, 2)
            self.assertIn('exists', r.stderr)
            self.assertEqual(cli('--batch', 'cousin-a', '--force').returncode, 0)
            r = cli('--batch', 'Bad Name', '--force')
            self.assertEqual(r.returncode, 2)
            self.assertIn('--batch', r.stderr)
            r = subprocess.run([sys.executable, CLI, b, '--mapping', os.path.join(d, 'nope.json'), '--batch', 'x', '--out', o, '--force'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)

    def test_cli_turns_broken_workbooks_into_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            b, m, o = (os.path.join(d, n) for n in ('b.xlsx', 'm.json', 'o.jsonl'))
            with open(m, 'w') as f:
                json.dump(MAP, f)
            for body, shared in (('<row r="1"><c r="A1" t="s"><v>7</v></c></row>', True),      # IndexError
                                 ('<row r="1"><c r="A1"', False),                                # ET.ParseError
                                 ('<row r="1"><c r="A1" t="s"><v>x</v></c></row>', True)):       # ValueError
                raw_sheet(b, body, shared=shared)
                r = subprocess.run([sys.executable, CLI, b, '--mapping', m, '--batch', 'x', '--out', o, '--force'], capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, body)
                self.assertNotIn('Traceback', r.stderr)


def raw_sheet(path, rows_xml, shared=False):
    """A one-sheet workbook named S whose sheetData is rows_xml as given."""
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('xl/workbook.xml', '<workbook %s xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>' % ns)
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        if shared:
            z.writestr('xl/sharedStrings.xml', '<sst %s><si><t>only</t></si></sst>' % ns)
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (ns, rows_xml))


if __name__ == '__main__':
    unittest.main()
