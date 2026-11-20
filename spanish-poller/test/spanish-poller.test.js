'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { parseCases, parseDehuRows, dehuLoadState, diff, senderAllowed, isoDate, summarize } = require('../gwt_spanish_poller.js');

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

// Shape of a real pending list: calendar rows first, then a header and ids that are not hex
// (14 digits, a letter plus digits) next to a NIF column. Values are made up.
const PENDING_ROWS = [
  ['26', '27', '28', '29', '30', '31', '1'],
  ['Columna ampliable', 'Identificador', 'Concepto', 'Organismo emisor', 'Titular', 'Fecha disposición', 'Tipo de notificación', '', 'Acciones'],
  ['', '', '11112222333344', 'ACUERDO DE ALTERACIÓN DE ...', 'Organismo A', '00000000T', '16/07/2024', 'Postal', '', 'Aceptar\nRechazar'],
  ['', 'Organismo raíz Ministerio A Fecha caducidad No procede '],
  ['', '', 'N123456789', 'REGIMENES ...', 'Organismo B', '00000000T', '26/11/2025', 'Postal', '', 'Aceptar\nRechazar'],
];

test('parseDehuRows reads ids that are not hex from the Identificador column', () => {
  const items = parseDehuRows(PENDING_ROWS);
  assert.deepStrictEqual(items.map(i => i.id), ['11112222333344', 'N123456789']);
  assert.strictEqual(items[0].sender, 'Organismo A');
  assert.strictEqual(items[0].date, '16/07/2024');
  assert.strictEqual(items[0].root, 'Ministerio A');
  assert.strictEqual(items[1].subject, 'REGIMENES ...');
});

test('dehuLoadState: loaded on a full list, did not load on a list cut after the header', () => {
  assert.strictEqual(dehuLoadState(parseDehuRows(PENDING_ROWS), false), 'loaded');
  assert.strictEqual(dehuLoadState(parseDehuRows(PENDING_ROWS.slice(0, 2)), false), 'did not load');
  assert.strictEqual(dehuLoadState(parseDehuRows([]), false), 'did not load');
  assert.strictEqual(dehuLoadState([], true), 'empty');
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

test('dump saves error pages without --dump, and nothing else', async () => {
  const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
  const { dump, setErrorDumpDir } = require('../gwt_spanish_poller.js');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'poller-err-'));
  const page = { content: async () => '<p>x</p>', innerText: async () => 'x', screenshot: async () => {} };
  setErrorDumpDir(dir);
  await dump(page, null, 'dehu-0-home');
  assert.deepStrictEqual(fs.readdirSync(dir), []);
  await dump(page, null, 'dehu-err-login');
  assert.deepStrictEqual(fs.readdirSync(dir).sort(), ['dehu-err-login.html', 'dehu-err-login.txt']);
  setErrorDumpDir(null);
  fs.rmSync(dir, { recursive: true, force: true });
});

// ---------- DEHú tabs read in full, or said not to be ----------
// Made-up rows in the shape DEHú renders: calendar rows, a header, a data row per notification with
// empty cells in front, and an expandable detail row after each.
const CAL = [['26', '27', '28', '29', '30', '31', '1'], ['2', '3', '4', '5', '6', '7', '8']];
const PENDING_HEAD = ['Columna ampliable', 'Identificador', 'Concepto', 'Organismo emisor', 'Titular', 'Fecha disposición', 'Tipo de notificación', '', 'Acciones'];
const DONE_HEAD = ['Columna ampliable', 'Identificador', 'Organismo emisor', 'Titular', 'Vínculo', 'Fecha disposición', 'Tipo de notificación', '', 'Estado', 'Acciones'];
const COMM_HEAD = ['Columna ampliable', 'Identificador', 'Concepto', 'Organismo emisor', 'Titular', 'Vínculo', 'Fecha disposición', 'Estado', 'Acciones'];
const pendingRow = (id, sender, date) => [
  ['', '', id, 'Requerimiento', sender, '00000000T', date, 'Electrónica', '', 'Aceptar\nRechazar'],
  ['', `Organismo raíz Organismo raíz de prueba Fecha caducidad ${date} `],
];
const doneRow = (id, sender, date) => [
  ['', '', id, sender, '00000000T', 'Titular', date, 'Postal', '', 'Aceptada', 'Detalle'],
  ['', `Concepto Notificación de prueba Organismo raíz Ministerio de prueba Fecha de estado final ${date} `],
];
const commRow = (id, date) => [['', '', id, 'Comunicación de prueba', 'Organismo C', '00000000T', 'Titular', date, 'Leída', 'Detalles']];
const HEX = (n) => n.toString(16).padStart(20, '0');

// A fake DEHú page: the inbox is already open, tabs switch on the same clicks the poller makes, and
// each tab answers with its rows. Waits return at once.
function fakeDehu({ counters, pending, done, comm, resetOnce = false }) {
  let tab = 'home', resets = resetOnce ? 1 : 0;
  const lists = { pending, done, comm };
  const total = (rows) => { const n = rows ? rows.filter(r => r.length > 2 && r[2]).length : 0; return n ? `1-${n} de ${n} elementos` : ''; };
  const body = () => (tab === 'reset' ? 'This site can’t be reached\nERR_CONNECTION_RESET\n'
    : `Notificaciones pendientes\n${counters.pending}\nNotificaciones realizadas\n${counters.done}\n`);
  const clickable = (fn) => ({ first: () => clickable(fn), click: async () => fn(), count: async () => 0, getByText: () => clickable(() => {}) });
  return {
    goto: async () => { tab = 'home'; },
    waitForTimeout: async () => {},
    innerText: async () => body(),
    url: () => 'https://dehu.example/es/home-view',
    content: async () => '',
    screenshot: async () => {},
    locator: (sel) => clickable(() => { if (sel === '#pane-button-1') tab = 'done'; }),
    getByText: (t) => clickable(() => {
      if (/pendientes/.test(t) && resets) { resets--; tab = 'reset'; return; }
      tab = /pendientes/.test(t) ? 'pending' : /Comunicaciones/.test(t) ? 'comm' : tab;
    }),
    evaluate: async () => {
      const l = lists[tab];
      return { rows: l ? [...CAL, ...l.rows] : [], total: l ? (l.total != null ? l.total : total(l.rows)) : null };
    },
  };
}

// The 20-11 run: two pending rows on the page, and an accepted tab whose counter says 6 with only 3 rows shown.
const SHORT_DONE = {
  counters: { pending: 2, done: 6 },
  pending: { rows: [PENDING_HEAD, ...pendingRow('0a00000000000000aa01', 'Ayuntamiento de prueba', '20/11/2026'), ...pendingRow('0a00000000000000aa02', 'Ayuntamiento de prueba', '19/11/2026')] },
  done: { rows: [DONE_HEAD, ...doneRow(HEX(1), 'Organismo A', '09/11/2026'), ...doneRow('11112222333344', 'Organismo B', '27/10/2026'), ...doneRow(HEX(2), 'Organismo C', '04/11/2026')], total: '1-6 de 6 elementos' },
  comm: { rows: [COMM_HEAD, ...commRow(HEX(3), '13/11/2026')] },
};

test('pollDehu: an accepted tab that reads short does not throw the pending list away', async () => {
  const { pollDehu } = require('../gwt_spanish_poller.js');
  const d = await pollDehu(fakeDehu(SHORT_DONE), null);
  assert.deepStrictEqual(d.pending.map(n => n.id), ['0a00000000000000aa01', '0a00000000000000aa02']);
  assert.strictEqual(d.tabs.pending.state, 'read');
  assert.strictEqual(d.tabs.pending.expected, 2);
  assert.strictEqual(d.done.length, 3);
  assert.strictEqual(d.tabs.done.state, 'partial');
  assert.strictEqual(d.communications.length, 1);
});

test('pollDehu: a pending list that does not parse is «not read», never 0 pending', async () => {
  const { pollDehu, dehuTabErrors } = require('../gwt_spanish_poller.js');
  const broken = { ...SHORT_DONE, pending: { rows: [PENDING_HEAD] } };
  const d = await pollDehu(fakeDehu(broken), null);
  assert.strictEqual(d.tabs.pending.state, 'not read');
  assert.match(d.tabs.pending.note, /tab counter says 2/);
  assert.strictEqual(d.done.length, 3);
  assert.match(dehuTabErrors(d.tabs).join('\n'), /pending not read: tab counter says 2/);
});

test('pollDehu: a connection reset after the pending-tab click is reloaded once, not a lost read', async () => {
  const { pollDehu } = require('../gwt_spanish_poller.js');
  const d = await pollDehu(fakeDehu({ ...SHORT_DONE, resetOnce: true }), null);
  assert.deepStrictEqual(d.counters, { pending: 2, done: 6 });
  assert.strictEqual(d.pending.length, 2);
});

test('dehuTabRead: the pending count comes from the tab counter and is checked against the rows', () => {
  const { dehuTabRead } = require('../gwt_spanish_poller.js');
  const two = parseDehuRows(SHORT_DONE.pending.rows);
  assert.strictEqual(dehuTabRead(two, { counter: 2 }).state, 'read');
  assert.strictEqual(dehuTabRead(two.slice(0, 1), { counter: 2 }).state, 'partial');
  assert.strictEqual(dehuTabRead([], { counter: 2 }).state, 'not read');
  assert.strictEqual(dehuTabRead([], { counter: 0 }).state, 'read');
  assert.strictEqual(dehuTabRead([], { counter: null, empty: true }).state, 'read');
  assert.strictEqual(dehuTabRead([], { counter: null }).state, 'not read');
  // The tab counter wins over the list's own total line.
  assert.strictEqual(dehuTabRead(two, { counter: 3, total: '1-2 de 2 elementos' }).state, 'partial');
  // A full first page with more behind it: the page was read, the rest was not.
  const ten = Array.from({ length: 10 }, (_, k) => ({ id: HEX(k) }));
  const paged = dehuTabRead(ten, { counter: 14, total: '1-10 de 14 elementos' });
  assert.deepStrictEqual([paged.state, paged.paged, paged.note], ['partial', true, '4 more on later pages, not read']);
});

test('dehuTabErrors: a short pending list is an error, a short accepted list is not', () => {
  const { dehuTabErrors } = require('../gwt_spanish_poller.js');
  const read = { state: 'read' };
  assert.deepStrictEqual(dehuTabErrors({ pending: read, done: { state: 'partial', note: 'x' }, communications: read }), []);
  assert.strictEqual(dehuTabErrors({ pending: { state: 'partial', note: 'x' }, done: read, communications: read }).length, 1);
  assert.deepStrictEqual(dehuTabErrors({ pending: { state: 'partial', paged: true, note: 'x' }, done: read, communications: read }), []);
  assert.strictEqual(dehuTabErrors({ pending: read, done: { state: 'not read', note: 'x' }, communications: read }).length, 1);
});

test('summarize: DEHú kept from an earlier poll says NOT READ, never «0 pending» or a plain «no changes»', () => {
  const snap = {
    polled_at: '2026-11-20T10:04:10Z', dehu_stale: true, dehu_read_at: '2026-11-17T11:00:40Z',
    sede_stale: true, sede_read_at: '2026-11-17T11:00:40Z', sede: { cases: {} },
    dehu: { pending: [], done: [{ id: HEX(1), cells: [] }], communications: [] },
  };
  const text = summarize('p', snap, [], ['dehu: login did not reach the inbox']);
  assert.doesNotMatch(text, /0 pending/);
  assert.doesNotMatch(text, /^no changes$/m);
  assert.match(text, /dehu: NOT READ this run; the snapshot read at 2026-11-17T11:00:40Z is kept/);
  assert.match(text, /sede: NOT READ this run/);
  assert.match(text, /not read this run: sede, dehu/);
});

test('summarize: a tab read short says so, and the pending count is the one read now', () => {
  const snap = {
    polled_at: 't', sede: { cases: {} },
    dehu: {
      pending: [{ id: 'a', sender: 'X', subject: 'Y', cells: [] }], done: [{ id: 'b', cells: [] }, { id: 'old', cells: [] }], communications: [],
      tabs: { pending: { state: 'read', expected: 1 }, done: { state: 'partial', note: 'tab counter says 6, 1 rows read', kept: 1 }, communications: { state: 'read' } },
    },
  };
  const text = summarize('p', snap, [], []);
  assert.match(text, /dehu: 1 pending notifications, accepted NOT FULLY READ \(tab counter says 6, 1 rows read\), 0 communications/);
  assert.match(text, /no changes in what was read/);
  assert.match(text, /not read this run: dehu accepted/);
});

test('keepUnreadRows: rows of a tab read short stay in the snapshot and are not reported again', () => {
  const { keepUnreadRows } = require('../gwt_spanish_poller.js');
  const prev = { pending: [], done: [{ id: 'old1' }, { id: 'old2' }, { id: 'n1' }], communications: [{ id: 'c1' }] };
  const cur = {
    pending: [{ id: 'p1' }], done: [{ id: 'n1' }, { id: 'n2' }], communications: [],
    tabs: { pending: { state: 'read' }, done: { state: 'partial' }, communications: { state: 'not read' } },
  };
  const kept = keepUnreadRows(prev, cur);
  assert.deepStrictEqual(kept.done.map(x => x.id), ['n1', 'n2', 'old1', 'old2']);
  assert.deepStrictEqual(kept.communications.map(x => x.id), ['c1']);
  assert.strictEqual(kept.tabs.done.kept, 2);
  const ev = diff({ dehu: prev }, { dehu: kept });
  assert.deepStrictEqual(ev.map(e => [e.kind, e.item.id]), [['notification_pending', 'p1'], ['notification_done', 'n2']]);
});

test('readAt: a kept side names when it was last read for real', () => {
  const { readAt } = require('../gwt_spanish_poller.js');
  assert.strictEqual(readAt({ polled_at: 'a' }, 'dehu'), 'a');
  assert.strictEqual(readAt({ polled_at: 'b', dehu_stale: true, dehu_read_at: 'a' }, 'dehu'), 'a');
  assert.strictEqual(readAt({ polled_at: 'b', dehu_stale: true }, 'dehu'), null);
});

test('sedeNoCountError names the sede\'s own «Página no encontrada» page', () => {
  const { sedeNoCountError } = require('../gwt_spanish_poller.js');
  const from = new Date(2026, 9, 22), to = new Date(2026, 10, 20);
  assert.strictEqual(sedeNoCountError('Trámites\nPágina no encontrada\n', from, to), 'sede: the sede answered «Página no encontrada» to the search 22/10/2026-20/11/2026');
  assert.strictEqual(sedeNoCountError('x', from, to), 'sede: search 22/10/2026-20/11/2026 returned no result count');
});
