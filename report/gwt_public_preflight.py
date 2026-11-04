"""Offline structural checks for a deceased-only CSV. Never an upload approval."""
import collections
import csv
import datetime as dt
import json
import re
import unicodedata
from urllib.parse import urlsplit

FIELDS = ('estado id_gramps nombre_literal_acta apellido_literal_acta tipo_fecha_principal '
          'fecha_principal indicador_fecha tipo_fecha_secundaria fecha_secundaria '
          'indicador_fecha_secundaria lugar_documentado fuente referencia_exacta url_fuente '
          'cita_gramps nota_evidencia lecturas_coincidentes criterio_fallecido '
          'discrepancias_no_exportadas relaciones_exportadas control_apellido_literal fecha_corte').split()


def date_ok(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def source_url_ok(value):
    if any(c.isspace() or unicodedata.category(c) == 'Cc' for c in value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        return (parsed.scheme == 'https' and bool(host) and
                parsed.username is None and parsed.password is None and
                not any(c in host for c in ('?', '#', '/', '\\')))
    except ValueError:
        return False


def cutoff_ok(value):
    if not isinstance(value, str):
        return False
    if date_ok(value):
        return True
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return 'T' in value and parsed.tzinfo is not None
    except ValueError:
        return False


def validate_rows(rows, *, surname_required=False, excluded_ids=()):
    """Report row numbers and fixed reason codes only, never echo private values.

    Text claiming two readings/deceased status is not authenticated evidence.
    Even a structurally valid row remains pending-review and cannot be published.
    """
    results = []
    exclusions = {value.strip() for value in excluded_ids}
    ids, identities = collections.defaultdict(list), collections.defaultdict(list)
    for number, row in enumerate(rows, 2):
        errors = []
        if not isinstance(row, dict) or set(row) != set(FIELDS) or any(not isinstance(v, str) for v in row.values()):
            results.append({'row': number, 'errors': ['schema'], 'state': 'blocked'})
            continue
        if any(any(unicodedata.category(c) == 'Cc' for c in v) for v in row.values()):
            errors.append('control-character')
        if row['estado'] != 'ELEGIBLE_NUCLEO;VALIDAR_ESQUEMA_DESTINO':
            errors.append('status-not-deceased-candidate')
        for key in ['id_gramps', 'nombre_literal_acta', 'fuente', 'referencia_exacta', 'url_fuente',
                    'cita_gramps', 'nota_evidencia', 'discrepancias_no_exportadas']:
            if not row[key].strip():
                errors.append('missing-' + key)
        if not source_url_ok(row['url_fuente']):
            errors.append('source-url')
        surname = row['apellido_literal_acta']
        if not surname.strip():
            if surname_required:
                errors.append('destination-requires-surname')
            if row['control_apellido_literal'] != 'VACIO_NO_INFERIR_APELLIDO':
                errors.append('surname-control')
        elif row['control_apellido_literal'] != 'literal':
            errors.append('surname-control')
        if row['relaciones_exportadas'] != 'NINGUNA':
            errors.append('relationships-forbidden')
        if row['lecturas_coincidentes'] != '2+; concordancia de nombre y fecha documentada en nota':
            errors.append('two-readings-claim-missing')
        if row['criterio_fallecido'] not in ('Nacimiento literal >110 años', 'Sepultura documentada'):
            errors.append('deceased-basis-unsupported')
        for suffix, kinds in [('', ('Birth', 'Death')), ('_secundaria', ('Baptism', 'Burial'))]:
            kind = row['tipo_fecha_principal'] if not suffix else row['tipo_fecha_secundaria']
            value = row['fecha_principal'] if not suffix else row['fecha_secundaria']
            indicator = row['indicador_fecha' + suffix]
            if kind not in kinds or indicator not in ('exacta', 'antes de') or not date_ok(value):
                errors.append('date-contract' + suffix)
        if not cutoff_ok(row['fecha_corte']):
            errors.append('cutoff-date')
        if row['id_gramps'].strip() in exclusions:
            errors.append('explicit-exclusion')
        ids[row['id_gramps'].strip()].append(len(results))
        # Exact literal identity only. No surname inference, fuzzy merge or relation inference.
        identity = tuple(row[k].strip() for k in ('nombre_literal_acta', 'apellido_literal_acta',
                                                'tipo_fecha_principal', 'fecha_principal', 'indicador_fecha'))
        identities[identity].append(len(results))
        results.append({'row': number, 'errors': errors, 'state': 'blocked' if errors else 'pending-review'})
    for groups, code in [(ids, 'duplicate-id'), (identities, 'duplicate-literal-identity')]:
        for indexes in groups.values():
            if len(indexes) > 1:
                for i in indexes:
                    results[i]['errors'].append(code)
                    results[i]['state'] = 'blocked'
    if not results:
        results.append({'row': 0, 'errors': ['empty-input'], 'state': 'blocked'})
    return {'format': 'gwt-public-preflight/1', 'publish_allowed': False,
            'pending_requirements': ['independent-source-readings', 'deceased-evidence',
                                     'destination-contract', 'destination-deduplication'], 'rows': results}


def run(path, surname_required=False, evidence_path=None, excluded_ids=(), max_search_age_days=7):
    with open(path, newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != set(FIELDS):
            raise ValueError('schema')
        rows = list(reader)
        if evidence_path:
            with open(evidence_path, encoding='utf-8') as evidence_stream:
                evidence = json.load(evidence_stream)
            report = wikitree_contract(rows, evidence, excluded_ids=excluded_ids, max_search_age_days=max_search_age_days)
        else:
            report = validate_rows(rows, surname_required=surname_required, excluded_ids=excluded_ids)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 2 if any(row['errors'] for row in report['rows']) else 0


def wikitree_contract(rows, evidence, *, excluded_ids=(), max_search_age_days=7):
    """Check explicit attestations, not their authenticity. Publication stays disabled."""
    report = validate_rows(rows, surname_required=True, excluded_ids=excluded_ids)
    if not isinstance(evidence, dict) or set(evidence) != {'format', 'people'} or evidence['format'] != 'gwt-public-evidence/1' or not isinstance(evidence['people'], dict):
        for result in report['rows']:
            result['errors'].append('evidence-schema')
            result['state'] = 'blocked'
        return report
    for row, result in zip(rows, report['rows']):
        if not isinstance(row, dict) or set(row) != set(FIELDS) or any(not isinstance(v, str) for v in row.values()):
            continue
        errors = result['errors']
        person_id = row['id_gramps']
        ev = evidence['people'].get(person_id)
        if not isinstance(ev, dict):
            errors.append('missing-evidence')
            ev = {}
        record = ev.get('record')
        if not isinstance(record, dict) or not ((isinstance(record.get('ark'), str) and record['ark'].strip().startswith('ark:/')) or all(isinstance(record.get(k), str) and record[k].strip() for k in ('film', 'image'))):
            errors.append('record-locator')
        readings = ev.get('readings')
        expected = {k: row[k] for k in ('nombre_literal_acta', 'apellido_literal_acta', 'tipo_fecha_principal', 'fecha_principal', 'indicador_fecha', 'tipo_fecha_secundaria', 'fecha_secundaria', 'indicador_fecha_secundaria')}
        valid = isinstance(readings, list) and len(readings) >= 2
        readers = set()
        if valid:
            for reading in readings:
                if not isinstance(reading, dict) or not isinstance(reading.get('reader'), str) or not reading['reader'].strip() or reading.get('record') != record or reading.get('values') != expected:
                    valid = False
                    break
                readers.add(reading['reader'].strip())
        if not valid or len(readers) < 2:
            errors.append('structured-readings-disagree')
        cutoff = row['fecha_corte'][:10]
        birth = row['fecha_principal']
        cutoff_date = dt.date.fromisoformat(cutoff) if date_ok(cutoff) else None
        birth_date = dt.date.fromisoformat(birth) if date_ok(birth) else None
        age = None
        if birth_date and cutoff_date and row['tipo_fecha_principal'] == 'Birth':
            age = cutoff_date.year - birth_date.year - ((cutoff_date.month, cutoff_date.day) < (birth_date.month, birth_date.day))
        secondary = row['fecha_secundaria']
        secondary_date = dt.date.fromisoformat(secondary) if date_ok(secondary) else None
        proof_dates = []
        if row['tipo_fecha_principal'] == 'Death' and birth_date:
            proof_dates.append(birth_date)
        if row['tipo_fecha_secundaria'] == 'Burial' and secondary_date:
            proof_dates.append(secondary_date)
        if cutoff_date and any(value > cutoff_date for value in proof_dates):
            errors.append('future-evidence-event')
        death_event = cutoff_date is not None and any(value <= cutoff_date for value in proof_dates)
        over_110 = (birth_date is not None and cutoff_date is not None and row['tipo_fecha_principal'] == 'Birth'
                    and (cutoff_date.year, cutoff_date.month, cutoff_date.day) >
                    (birth_date.year + 110, birth_date.month, birth_date.day))
        if not death_event and not over_110:
            errors.append('deceased-not-established')
        if ev.get('primary_date_basis') == 'secondary-only' and row['indicador_fecha'] != 'antes de':
            errors.append('secondary-event-requires-before')
        if ev.get('primary_date_basis') not in ('primary-record', 'secondary-only'):
            errors.append('primary-date-basis')
        if birth_date and birth_date.year < 1700:
            certification = ev.get('owner_certification')
            certified = (isinstance(certification, dict) and certification.get('pre1700_self_certified') is True
                         and date_ok(certification.get('date', '')) and cutoff_date is not None
                         and dt.date.fromisoformat(certification['date']) <= cutoff_date
                         and isinstance(certification.get('reference'), str) and bool(certification['reference'].strip()))
            if not certified:
                errors.append('pre1700-held-for-owner-order')
        over_150 = (birth_date is not None and cutoff_date is not None and row['tipo_fecha_principal'] == 'Birth'
                    and (cutoff_date.year, cutoff_date.month, cutoff_date.day) >
                    (birth_date.year + 150, birth_date.month, birth_date.day))
        if over_150 and ev.get('privacy') != 'Open':
            errors.append('open-profile-required')
        search = ev.get('match_search')
        if not isinstance(search, dict) or search.get('completed') is not True or not isinstance(search.get('matches'), list) or not cutoff_ok(search.get('checked_at', '')):
            errors.append('match-search-required')
        else:
            if search['matches']:
                errors.append('destination-match-no-create')
            checked = dt.datetime.fromisoformat(search['checked_at'].replace('Z', '+00:00')) if 'T' in search['checked_at'] else dt.datetime.combine(dt.date.fromisoformat(search['checked_at']), dt.time(), dt.timezone.utc)
            if checked.tzinfo is None:
                errors.append('match-search-time')
            elif cutoff_date is not None:
                cutoff_time = dt.datetime.fromisoformat(row['fecha_corte'].replace('Z', '+00:00')) if 'T' in row['fecha_corte'] and cutoff_ok(row['fecha_corte']) else dt.datetime.combine(cutoff_date, dt.time(), dt.timezone.utc)
                age_days = (cutoff_time - checked).total_seconds() / 86400
                if age_days < 0:
                    errors.append('match-search-future')
                elif age_days > max_search_age_days:
                    errors.append('match-search-stale')
        if ev.get('relationships') != []:
            errors.append('evidence-relationships-forbidden')
        if errors:
            result['state'] = 'blocked'
    report['destination'] = 'wikitree'
    report['execution_constraints'] = {'max_creates_per_step': 1, 'pace': 'moderate; site-specific approval required'}
    return report
