'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { parseCases, parseDehuRows, diff, senderAllowed, isoDate, summarize } = require('../gwt_poller.js');

const CASE = (id, status, created = '19-10-2026') => ({ id, procedure: 'Certificado de nacimiento (general)', status, created, applicant: 'A', subject: 'B' });

test('parseCases keeps only rows that start with a case id', () => {
  const cases = parseCases([
    ['Expediente', 'Procedimiento', 'Estado'],
    [' 20261019/000004 ', 'Certificado de nacimiento (general)', 'En trámite', '19-10-2026', 'A', 'B'],
  ]);
  assert.deepStrictEqual(Object.keys(cases), ['20261019/000004']);
  assert.strictEqual(cases['20261019/000004'].status, 'En trámite');
});

test('parseDehuRows lines columns up on the id and reads the expandable row', () => {
  const rows = [
    ['28', '29', '30', '1', '2', '3', '4'],
    ['Columna ampliable', 'Identificador', 'Concepto', 'Organismo emisor', 'Titular', 'Vínculo', 'Fecha disposición', 'Estado', 'Acciones'],
    ['', '', '0123456789abcdef0123', 'Comunicación desde Dicireg', 'S.G. de Nacionalidad y Estado Civil', '', 'Destinatario', '03/03/2025', 'Leída', 'Detalles'],
    ['', 'Organismo raíz Ministerio X Fecha leída 07/03/2025 '],
  ];
  const [it] = parseDehuRows(rows);
  assert.strictEqual(it.id, '0123456789abcdef0123');
  assert.strictEqual(it.subject, 'Comunicación desde Dicireg');
  assert.strictEqual(it.sender, 'S.G. de Nacionalidad y Estado Civil');
  assert.strictEqual(it.date, '03/03/2025');
  assert.strictEqual(it.status, 'Leída');
  assert.strictEqual(it.root, 'Ministerio X');
  assert.strictEqual(it.final_date, '07/03/2025');
});

test('diff: first poll is a baseline and still reports pending notifications', () => {
  const ev = diff(null, { dehu: { pending: [{ id: 'abcdef0123456789' }] } });
  assert.deepStrictEqual(ev.map(e => e.kind), ['baseline', 'notification_pending']);
});

test('diff: same snapshot means no change', () => {
  const s = { sede: { since: '01/05/2026', cases: { '20261019/000001': CASE('20261019/000001', 'En trámite') } }, dehu: { pending: [], done: [], communications: [] } };
  assert.deepStrictEqual(diff(s, JSON.parse(JSON.stringify(s))), []);
});

test('diff: new case, status change, gone case and new communication', () => {
  const prev = {
    sede: { since: '01/05/2026', cases: { '20261019/000001': CASE('20261019/000001', 'En trámite'), '20261019/000002': CASE('20261019/000002', 'En trámite') } },
    dehu: { pending: [], done: [], communications: [] },
  };
  const cur = {
    sede: { since: '01/05/2026', cases: { '20261019/000001': CASE('20261019/000001', 'Cerrada - Resolución positiva'), '20261020/000003': CASE('20261020/000003', 'En trámite', '20-10-2026') } },
    dehu: { pending: [], done: [], communications: [{ id: 'abcdef0123456789', cells: [] }] },
  };
  assert.deepStrictEqual(diff(prev, cur).map(e => e.kind).sort(), ['case_gone', 'case_new', 'case_status', 'communication_new']);
});

test('diff: a case older than the search window aged out, it did not disappear', () => {
  const prev = { sede: { since: '01/04/2026', cases: { '20260402/000001': CASE('20260402/000001', 'En trámite', '02-04-2026') } } };
  const cur = { sede: { since: '01/05/2026', cases: {} } };
  assert.deepStrictEqual(diff(prev, cur), []);
});

test('senderAllowed matches the allow-list in sender, subject or cells, ignoring case', () => {
  assert.ok(senderAllowed({ sender: 'S.G. de Nacionalidad y Estado Civil', cells: [] }, ['nacionalidad y estado civil']));
  assert.ok(!senderAllowed({ sender: 'Ayuntamiento', subject: 'Multa', cells: [] }, ['dicireg']));
});

test('isoDate reads both separators', () => {
  assert.strictEqual(isoDate('19-10-2026'), '2026-10-19');
  assert.strictEqual(isoDate('19/10/2026'), '2026-10-19');
  assert.strictEqual(isoDate('x'), null);
});

test('summarize lists pending notifications and says when nothing changed', () => {
  const snap = { polled_at: 't', sede: { cases: {} }, dehu: { pending: [{ sender: 'X', subject: 'Y', cells: [] }], done: [], communications: [] } };
  const text = summarize('p', snap, [], []);
  assert.match(text, /pending: X · Y/);
  assert.match(text, /no changes/);
});
