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


HEAD = ['Given', 'Surname', 'Sex', 'Born', 'Birth place', 'Died', 'Death place', 'Source', 'Page']
MAP = {'ruling': 'R-1', 'sheets': [{'sheet': 'People', 'header_row': 1, 'columns': {
    'first_name': 'Given', 'surname': 'Surname', 'gender': 'Sex', 'birth_date': 'Born', 'birth_place': 'Birth place',
    'death_date': 'Died', 'death_place': 'Death place', 'source': 'Source', 'page': 'Page'}}]}


def run(rows, people=(), mapping=MAP, inline=False):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'b.xlsx')
        make_xlsx(p, {'People': [HEAD] + rows}, inline)
        return X.import_rows(X.read_workbook(p), mapping, list(people))


class T(unittest.TestCase):
    def test_reads_shared_and_inline_strings(self):
        for inline in (False, True):
            ops, rep = run([['Ana', 'Perez', 'F', '1901', 'Madrid', '', '', 'Parish book', 'f 3']], inline=inline)
            self.assertEqual(rep['people'], 1)
            self.assertEqual(ops[0]['first_name'], 'Ana')

    def test_dates_are_never_guessed(self):
        self.assertEqual(X.parse_date('1901-03-14')[0], '1901-03-14')
        self.assertEqual(X.parse_date('14/03/1901')[0], '1901-03-14')
        self.assertEqual(X.parse_date('c. 1860')[0], 'abt 1860')
        self.assertEqual(X.parse_date('Mar 1901')[0], '1901-03')
        self.assertEqual(X.parse_date('?'), (None, None))
        self.assertEqual(X.parse_date('31/02/19xx')[0], None)
        self.assertEqual(X.parse_date('13/13/1901'), (None, 'impossible date'))
        self.assertEqual(X.parse_date('spring of the war'), (None, 'unreadable date'))

    def test_unreadable_date_is_kept_as_text_not_as_a_date(self):
        ops, rep = run([['Ana', 'Perez', 'F', 'spring of the war', '', '', '', 'Book', 'p1']])
        ev = [o for o in ops if o['op'] == 'add_event'][0]
        self.assertNotIn('date', ev)
        self.assertEqual(ev['description'], 'Date as written: spring of the war')
        self.assertEqual(rep['date_problems'][0]['reason'], 'unreadable date')

    def test_row_without_source_gets_a_note_and_is_reported(self):
        ops, rep = run([['Ana', 'Perez', 'F', '1901', '', '', '', '', '']])
        self.assertEqual(rep['no_source'], ['People!2'])
        self.assertTrue(any(o['op'] == 'add_note' and o['text'].startswith('[no source]') for o in ops))
        self.assertFalse(any(o['op'] == 'add_citation' for o in ops))

    def test_source_becomes_citation_on_person_and_events(self):
        ops, _ = run([['Ana', 'Perez', 'F', '1901', 'Madrid', '1970', 'Madrid', 'Parish book', 'f 3'],
                      ['Luis', 'Gil', 'M', '1899', 'Madrid', '', '', 'Parish book', 'f 4']])
        self.assertEqual(len([o for o in ops if o['op'] == 'add_source']), 1)   # one source, two citations
        cit = [o for o in ops if o['op'] == 'add_citation']
        self.assertEqual(len(cit), 2)
        self.assertEqual(len(cit[0]['attach']), 3)
        self.assertEqual(len([o for o in ops if o['op'] == 'add_place']), 1)

    def test_possible_duplicates_are_left_out_not_merged(self):
        tree = [{'id': 'P000007', 'name': 'ana perez', 'by': 1900}]
        ops, rep = run([['Ana', 'Pérez', 'F', '1901', '', '', '', 'Book', 'p'], ['Ana', 'Perez', 'F', '1950', '', '', '', 'Book', 'p']], tree)
        self.assertEqual(rep['duplicates'][0]['tree_ids'], ['P000007'])    # accent and 1 year apart
        self.assertEqual(rep['people'], 1)                                  # 1950 is a different person
        self.assertEqual(len([o for o in ops if o['op'] == 'add_person']), 1)

    def test_same_row_twice_in_the_workbook(self):
        ops, rep = run([['Ana', 'Perez', 'F', '1901', '', '', '', 'B', 'p'], ['Ana', 'Perez', 'F', '1901', '', '', '', 'B', 'p']])
        self.assertEqual(rep['people'], 1)
        self.assertEqual(rep['duplicates'][0]['same_as_row'], 'People!2')

    def test_incomplete_name_is_skipped_with_reason(self):
        ops, rep = run([['Ana', '', 'F', '1901', '', '', '', 'B', 'p']])
        self.assertEqual(ops, [])
        self.assertEqual(rep['skipped'][0]['reason'], 'name incomplete')

    def test_missing_column_names_the_column(self):
        bad = json.loads(json.dumps(MAP)); bad['sheets'][0]['columns']['source'] = 'Fuente'
        with self.assertRaises(X.ImportError_) as c:
            run([['Ana', 'Perez', 'F', '1901', '', '', '', 'B', 'p']], mapping=bad)
        self.assertIn('Fuente', str(c.exception))

    def test_every_op_has_a_ruling_and_unique_key(self):
        ops, _ = run([['Ana', 'Perez', 'F', '1901', 'Madrid', '1970', 'Madrid', 'B', 'p'], ['Luis', 'Gil', 'M', '1899', 'Madrid', '', '', 'B', 'q']])
        self.assertTrue(all(o['ruling'] == 'R-1' for o in ops))
        keys = [o['key'] for o in ops]
        self.assertEqual(len(keys), len(set(keys)))

    def test_output_passes_gwt_apply_validation(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'apply'))
        try:
            from gwt_apply.ops import validate
        except ImportError:
            self.skipTest('gwt-apply not importable (needs requests)')
        ops, _ = run([['Ana', 'Perez', 'F', '1901', 'Madrid', '1970', 'Madrid', 'Parish book', 'f 3'],
                      ['Luis', 'Gil', 'M', 'spring', '', '', '', '', '']])
        self.assertEqual(validate(ops), [])

    def test_impossible_calendar_dates_are_refused(self):
        for bad in ('31/02/1901', '99 March 1901', '2026-02-29', '1901-04-31', '0/3/1901', '29 Feb 1900', '31 Jun 1901', '1901-02-00', '00 March 1901', '0000', '1901-00', '1901-00-10', '00/03/1901', '0000-05-01', 'c. 0000'):
            self.assertEqual(X.parse_date(bad)[0], None, bad)
            self.assertEqual(X.parse_date(bad)[1], 'impossible date', bad)
        for good in ('29/02/1904', '2024-02-29', '29 Feb 1904', '1901-02'):
            self.assertIsNotNone(X.parse_date(good)[0], good)
        self.assertEqual(X.parse_date('circa 1901')[0], 'abt 1901')
        self.assertEqual(X.parse_date('ca. 1901')[0], 'abt 1901')

    def test_places_and_sources_that_slug_alike_keep_their_own_keys(self):
        ops, _ = run([['Ana', 'Perez', 'F', '1901', 'A-B', '', '', 'Register A-B', 'f1'],
                      ['Luis', 'Gil', 'M', '1899', 'A B', '', '', 'Register A B', 'f2']])
        places = [o for o in ops if o['op'] == 'add_place']
        srcs = [o for o in ops if o['op'] == 'add_source']
        self.assertEqual(sorted(p['name'] for p in places), ['A B', 'A-B'])
        self.assertEqual(sorted(s['title'] for s in srcs), ['Register A B', 'Register A-B'])
        ev = {o['person']: o['place'] for o in ops if o['op'] == 'add_event'}
        self.assertEqual(len(set(ev.values())), 2)
        cit = {o['attach'][0]['person']: o['source'] for o in ops if o['op'] == 'add_citation'}
        self.assertEqual(len(set(cit.values())), 2)
        keys = [o['key'] for o in ops]
        self.assertEqual(len(keys), len(set(keys)))

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
            ops, rep = X.import_rows(book, MAP, [], meta)
            self.assertEqual(rep['time_cells'], ['People!%d:Born' % r for r in (8, 9, 10, 11)])
            self.assertFalse(any(o.get('date') == '1900-01-01' for o in ops))
            self.assertEqual(rep['excel_dates'], 4)
            self.assertEqual(rep['formula_cells'], ['People!6:Born', 'People!7:Born'])   # a date-styled formula is still a formula
            self.assertEqual(meta[('People', 7, 3)], {'formula', 'date'})
            self.assertEqual(rep['error_cells'], ['People!5:Born'])
            by = {o['person']: o.get('date') for o in ops if o['op'] == 'add_event'}
            self.assertEqual(sorted(v for v in by.values() if v), ['1901', '1901-03-14', '1901-03-14', '1901-03-14', '1901-03-14', '1950'])

    def test_time_cell_that_looks_like_a_year_is_not_a_year(self):
        meta = {('People', 2, 3): {'time'}, ('People', 3, 3): {'time'}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'b.xlsx')
            make_xlsx(p, {'People': [HEAD, ['Ana', 'Perez', 'F', '1901', '', '', '', 'B', 'p'], ['Ana', 'Perez', 'F', '1950', '', '', '', 'B', 'p']]})
            tree = [{'id': 'P1', 'name': 'luis gil', 'by': 1901}]
            ops, rep = X.import_rows(X.read_workbook(p), MAP, tree, meta)
        self.assertEqual(rep['people'], 1)                      # the 1901 in a time cell gives no birth year: key says nd, not 1901
        self.assertEqual(len(rep['duplicates']), 1)             # the second is the same name with no year, reported not merged
        self.assertTrue(all('nd' in o['key'] for o in ops if o['op'] == 'add_person'))
        ev = [o for o in ops if o['op'] == 'add_event']
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

    def test_real_dump_dates_use_dateval(self):
        def dump(dv, **extra):
            d = {'dateval': dv}
            d.update(extra)
            return {'objects': {'people': [{'gramps_id': 'P1', 'primary_name': {'first_name': 'Ana', 'surname_list': [{'surname': 'Perez'}]},
                                            'birth_ref_index': 0, 'event_ref_list': [{'ref': 'h1'}]}], 'events': [{'handle': 'h1', 'date': d}]}}
        with tempfile.TemporaryDirectory() as dd:
            def tree(dv, **extra):
                p = os.path.join(dd, 't.json')
                json.dump(dump(dv, **extra), open(p, 'w'))
                return X.tree_people(p)
            t1800 = tree([14, 3, 1800, False])                       # no 'year' key at all, as in a bare dump
            self.assertEqual(t1800[0]['by'], 1800)
            _, rep = run([['Ana', 'Perez', 'F', '1950', '', '', '', 'B', 'p']], t1800)
            self.assertEqual((rep['people'], rep['duplicates']), (1, []))                # 1800 vs 1950 imports
            t1900 = tree([0, 0, 1900, False])
            _, rep = run([['Ana', 'Perez', 'F', '1901', '', '', '', 'B', 'p']], t1900)
            self.assertEqual(rep['duplicates'][0]['tree_ids'], ['P1'])                   # 1900 vs 1901 is a possible duplicate
            self.assertEqual(tree([1, 2, 1850, False, 3, 4, 1860, False])[0]['by'], 1850)  # range: first year
            for bad in ([], [1, 2], [1, 2, 'x', False], [1, 2, 0, False], [1, 2, True, False], None):
                self.assertIsNone(tree(bad)[0]['by'], bad)
            self.assertEqual(tree(None, year=1800)[0]['by'], 1800)   # no dateval: fall back to year

    def test_tree_dump_reader(self):
        dump = {'objects': {'people': [{'gramps_id': 'P1', 'primary_name': {'first_name': 'Ana', 'surname_list': [{'surname': 'Perez'}]},
                                        'birth_ref_index': 0, 'event_ref_list': [{'ref': 'h1'}]}],
                            'events': [{'handle': 'h1', 'date': {'dateval': [14, 3, 1901, False], 'year': 1901}}]}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 't.json.gz')
            with gzip.open(p, 'wt') as f:
                json.dump(dump, f)
            self.assertEqual(X.tree_people(p), [{'id': 'P1', 'name': 'ana perez', 'by': 1901}])

    def test_cli_writes_jsonl_and_never_touches_a_tree(self):
        with tempfile.TemporaryDirectory() as d:
            b, m, o = (os.path.join(d, n) for n in ('b.xlsx', 'm.json', 'o.jsonl'))
            make_xlsx(b, {'People': [HEAD, ['Ana', 'Perez', 'F', '1901', 'Madrid', '', '', 'B', 'p']]})
            json.dump(MAP, open(m, 'w'))
            r = subprocess.run([sys.executable, CLI, b, '--mapping', m, '--out', o], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('people 1', r.stdout)
            self.assertEqual(json.loads(open(o).readline())['op'], 'add_person')
            r = subprocess.run([sys.executable, CLI, b, '--mapping', os.path.join(d, 'nope.json')], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)


if __name__ == '__main__':
    unittest.main()
