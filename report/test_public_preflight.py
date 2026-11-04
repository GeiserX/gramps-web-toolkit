import copy
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from gwt_public_preflight import FIELDS, validate_rows, run


def fixture():
    row = dict.fromkeys(FIELDS, 'synthetic')
    row.update(estado='ELEGIBLE_NUCLEO;VALIDAR_ESQUEMA_DESTINO', id_gramps='SYNTH-1',
               nombre_literal_acta='Invented', apellido_literal_acta='Example',
               tipo_fecha_principal='Birth', fecha_principal='1800-01-02', indicador_fecha='antes de',
               tipo_fecha_secundaria='Baptism', fecha_secundaria='1800-01-03', indicador_fecha_secundaria='exacta',
               url_fuente='https://archive.example.invalid/record', relaciones_exportadas='NINGUNA',
               lecturas_coincidentes='2+; concordancia de nombre y fecha documentada en nota',
               criterio_fallecido='Nacimiento literal >110 años', control_apellido_literal='literal', fecha_corte='2026-01-01')
    return row


class PreflightTest(unittest.TestCase):
    def test_valid_is_only_pending_and_not_mutated(self):
        row = fixture(); before = copy.deepcopy(row)
        report = validate_rows([row]); self.assertEqual(report['rows'][0]['errors'], [])
        self.assertEqual(report['rows'][0]['state'], 'pending-review')
        self.assertIs(report['publish_allowed'], False); self.assertEqual(row, before)

    def test_each_guard_blocks(self):
        bad = {'estado': 'living', 'relaciones_exportadas': 'parent', 'lecturas_coincidentes': '1',
               'fuente': '', 'referencia_exacta': '', 'url_fuente': 'http://archive.invalid',
               'cita_gramps': '', 'nota_evidencia': '', 'criterio_fallecido': 'guess',
               'fecha_principal': '1800-02-30', 'indicador_fecha': 'after',
               'tipo_fecha_principal': 'Marriage', 'fecha_secundaria': 'bad',
               'indicador_fecha_secundaria': 'about', 'fecha_corte': 'bad',
               'control_apellido_literal': 'inferred'}
        for key, value in bad.items():
            with self.subTest(key=key):
                row = fixture(); row[key] = value
                self.assertEqual(validate_rows([row])['rows'][0]['state'], 'blocked')

    def test_surname_blank_never_filled_and_destination_can_block(self):
        row=fixture();row['apellido_literal_acta']='';row['control_apellido_literal']='VACIO_NO_INFERIR_APELLIDO'
        self.assertEqual(validate_rows([row])['rows'][0]['errors'], [])
        self.assertIn('destination-requires-surname', validate_rows([row], surname_required=True)['rows'][0]['errors'])
        self.assertEqual(row['apellido_literal_acta'], '')

    def test_duplicate_blocks_both_not_first_wins(self):
        a=fixture(); b=fixture();b['id_gramps']='SYNTH-2'
        r=validate_rows([a,b]);self.assertTrue(all('duplicate-literal-identity' in x['errors'] for x in r['rows']))
        b['nombre_literal_acta']='Other';b['id_gramps']=a['id_gramps']
        self.assertTrue(all('duplicate-id' in x['errors'] for x in validate_rows([a,b])['rows']))

    def test_cutoff_allows_aware_iso_timestamp_not_naive(self):
        row=fixture();row['fecha_corte']='2026-01-01T10:00:00Z'
        self.assertEqual(validate_rows([row])['rows'][0]['errors'], [])
        row['fecha_corte']='2026-01-01T10:00:00'
        self.assertIn('cutoff-date', validate_rows([row])['rows'][0]['errors'])

    def test_unicode_control_ranges_and_missing_hostname_block(self):
        for char in ('\x7f', '\x85', '\x9f', '\x00', '\n'):
            with self.subTest(char=ord(char)):
                row=fixture();row['nota_evidencia']='synthetic'+char+'text'
                self.assertIn('control-character',validate_rows([row])['rows'][0]['errors'])
        for url in ('https://?', 'https://#fragment', 'https:///path', 'https://', 'https://user@', 'https://[bad'):
            with self.subTest(url=url):
                row=fixture();row['url_fuente']=url
                self.assertIn('source-url',validate_rows([row])['rows'][0]['errors'])

    def test_empty_blocks(self):
        self.assertEqual(validate_rows([])['rows'][0]['state'], 'blocked')

    def test_schema_and_values_fail_closed(self):
        for row in [None, {}, {**fixture(), 'extra':'x'}, {**fixture(),'fuente':None}, {**fixture(),'fuente':'private\nvalue'}]:
            self.assertEqual(validate_rows([row])['rows'][0]['state'], 'blocked')

    def test_csv_output_has_only_codes_no_private_values(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'synthetic.csv';row=fixture();row['nota_evidencia']='DO_NOT_ECHO_SYNTHETIC_MARKER'
            with path.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerow(row)
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(run(path),0)
            self.assertNotIn(row['nota_evidencia'],out.getvalue());self.assertFalse(json.loads(out.getvalue())['publish_allowed'])
            path.write_text('estado,estado\nx,y\n')
            with self.assertRaises(ValueError):run(path)

if __name__=='__main__':unittest.main()

from gwt_public_preflight import wikitree_contract


def evidence(row):
    record={'ark':'ark:/synthetic/record'}
    values={k: row[k] for k in ('nombre_literal_acta','apellido_literal_acta','tipo_fecha_principal','fecha_principal','indicador_fecha','tipo_fecha_secundaria','fecha_secundaria','indicador_fecha_secundaria')}
    return {'format':'gwt-public-evidence/1','people':{row['id_gramps']:{'record':record,
        'readings':[{'reader':reader,'record':record.copy(),'values':values.copy()} for reader in ('synthetic-a','synthetic-b')],
        'primary_date_basis':'primary-record','privacy':'Open','relationships':[],
        'match_search':{'completed':True,'checked_at':'2026-01-01T00:00:00Z','matches':[]}}}}


class WikiTreeTest(unittest.TestCase):
    def test_contract_never_approves_upload_and_enforces_single_step(self):
        row=fixture();result=wikitree_contract([row],evidence(row))
        self.assertEqual(result['rows'][0]['errors'],[]);self.assertFalse(result['publish_allowed'])
        self.assertEqual(result['execution_constraints']['max_creates_per_step'],1)

    def test_each_attestation_gate(self):
        row=fixture()
        for key,bad,code in [('record',{},'record-locator'),('readings',[],'structured-readings-disagree'),
                             ('primary_date_basis','secondary-only','secondary-event-requires-before'),
                             ('privacy','Private','open-profile-required'),('relationships',['parent'],'evidence-relationships-forbidden'),
                             ('match_search',{'completed':True,'checked_at':'2026-01-01','matches':['synthetic-match']},'destination-match-no-create')]:
            with self.subTest(key=key):
                current=copy.deepcopy(row)
                if key=='primary_date_basis':current['indicador_fecha']='exacta'
                ev=evidence(current);ev['people'][current['id_gramps']][key]=bad
                self.assertIn(code,wikitree_contract([current],ev)['rows'][0]['errors'])

    def test_reader_identity_agreement_record_binding_and_exclusion(self):
        row=fixture()
        for field in ('reader','record','values'):
            ev=evidence(row);readings=ev['people'][row['id_gramps']]['readings']
            readings[1][field]=readings[0][field] if field=='reader' else {}
            self.assertIn('structured-readings-disagree',wikitree_contract([row],ev)['rows'][0]['errors'])
        self.assertIn('explicit-exclusion',wikitree_contract([row],evidence(row),excluded_ids=[row['id_gramps']])['rows'][0]['errors'])

    def test_age_threshold_and_pre1700_and_burial(self):
        for year,blocked in [('1917',True),('1916',False),('1915',False),('1699',True)]:
            row=fixture();row['fecha_principal']=year+'-01-02';row['fecha_corte']='2026-01-03'
            errors=wikitree_contract([row],evidence(row))['rows'][0]['errors']
            self.assertEqual(bool(errors),blocked)
        row=fixture();row['fecha_principal']='2000-01-02';row['tipo_fecha_principal']='Death';row['tipo_fecha_secundaria']='Burial'
        self.assertNotIn('deceased-not-established',wikitree_contract([row],evidence(row))['rows'][0]['errors'])
        row['apellido_literal_acta']='';row['control_apellido_literal']='VACIO_NO_INFERIR_APELLIDO'
        self.assertIn('destination-requires-surname',wikitree_contract([row],evidence(row))['rows'][0]['errors'])

    def test_malformed_evidence_is_blocked(self):
        row=fixture()
        for ev in (None,{}, {'format':'bad','people':{}}, {'format':'gwt-public-evidence/1','people':[]}):
            self.assertIn('evidence-schema',wikitree_contract([row],ev)['rows'][0]['errors'])

class ReviewGapsTest(unittest.TestCase):
    def test_exclusions_apply_without_evidence_and_trim_ids(self):
        row=fixture();row['id_gramps']=' SYNTH-1 '
        report=validate_rows([row], excluded_ids=['SYNTH-1'])
        self.assertIn('explicit-exclusion',report['rows'][0]['errors'])
        self.assertIn('explicit-exclusion',wikitree_contract([row],evidence(row),excluded_ids=['SYNTH-1'])['rows'][0]['errors'])

    def test_open_after_150th_anniversary_not_whole_151(self):
        row=fixture();row['fecha_principal']='1875-01-02';row['fecha_corte']='2025-01-03'
        ev=evidence(row);ev['people'][row['id_gramps']]['privacy']='Private'
        self.assertIn('open-profile-required',wikitree_contract([row],ev)['rows'][0]['errors'])

    def test_future_death_or_burial_does_not_prove_deceased(self):
        row=fixture();row.update(tipo_fecha_principal='Death',fecha_principal='2100-01-01',tipo_fecha_secundaria='Burial',fecha_secundaria='2100-01-02',fecha_corte='2026-01-01')
        self.assertIn('future-evidence-event',wikitree_contract([row],evidence(row))['rows'][0]['errors'])
        self.assertIn('deceased-not-established',wikitree_contract([row],evidence(row))['rows'][0]['errors'])

class PrivateReviewGapsTest(unittest.TestCase):
    def test_missing_evidence_accumulates_all_applicable_codes(self):
        row=fixture();row['fecha_principal']='1600-01-02'
        result=wikitree_contract([row],{'format':'gwt-public-evidence/1','people':{}})['rows'][0]
        for code in ['missing-evidence','record-locator','structured-readings-disagree','primary-date-basis','pre1700-held-for-owner-order','open-profile-required','match-search-required','evidence-relationships-forbidden']:
            self.assertIn(code,result['errors'])

    def test_search_freshness_and_future(self):
        row=fixture()
        for checked,code in [('2025-01-01','match-search-stale'),('2026-01-02','match-search-future')]:
            ev=evidence(row);ev['people'][row['id_gramps']]['match_search']['checked_at']=checked
            self.assertIn(code,wikitree_contract([row],ev)['rows'][0]['errors'])
        ev=evidence(row);ev['people'][row['id_gramps']]['match_search']['checked_at']='2025-12-20'
        self.assertNotIn('match-search-stale',wikitree_contract([row],ev,max_search_age_days=30)['rows'][0]['errors'])

    def test_pre1700_requires_dated_owner_certification_reference(self):
        row=fixture();row['fecha_principal']='1600-01-02';ev=evidence(row);person=ev['people'][row['id_gramps']]
        person['owner_certification']={'pre1700_self_certified':True,'date':'2025-01-01','reference':'synthetic-owner-approval'}
        result=wikitree_contract([row],ev)
        self.assertNotIn('pre1700-held-for-owner-order',result['rows'][0]['errors']);self.assertFalse(result['publish_allowed'])
        for invalid in ({}, {'pre1700_self_certified':True,'date':'2100-01-01','reference':'synthetic'}, {'pre1700_self_certified':True,'date':'2025-01-01','reference':''}):
            person['owner_certification']=invalid
            self.assertIn('pre1700-held-for-owner-order',wikitree_contract([row],ev)['rows'][0]['errors'])
