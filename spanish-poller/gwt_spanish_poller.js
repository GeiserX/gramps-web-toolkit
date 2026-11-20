'use strict';
/*
gwt-spanish-poller: watch Spanish civil-registry requests (sede.mjusticia «Estado de mis expedientes») and the
DEHú inbox for one or more people, each logging in with their own client certificate.

  gwt-spanish-poller [--config FILE] [--only ID] [--dump DIR]

Per person it snapshots the case list and the DEHú notifications and communications, diffs them against
the previous snapshot, writes <state_dir>/<id>.json and <state_dir>/<id>.txt, and prints the summary.
Exit 0: nothing changed. Exit 10: something changed. Exit 2: a login or a page failed and nothing
else changed (the failed part keeps its previous snapshot, and the summary says it was not read).

Read-only toward the administration: it never submits a form and never opens, accepts or rejects a
notification. Communications that are already readable are downloaded only when their sender matches
the configured allow-list.
*/
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, execFileSync } = require('child_process');

const EXIT_SAME = 0;
const EXIT_CHANGED = 10;
const EXIT_ERROR = 2;

const SEDE_URL = 'https://sede.mjusticia.gob.es/sereci/clave/initComoVaLoMio?lang=es_ES';
const DEHU_URL = 'https://dehu.redsara.es/';
const DEHU_HOME = 'https://dehu.redsara.es/es/home-view';
const CLAVE_CERT_BUTTON = /Acceso DNIe \/ Certificado electr[oó]nico|Access eIdentifier/;
// Hosts that ask for the client certificate during a Cl@ve login (pkcs12 mode only). Playwright
// proxies TLS for every listed origin, and listing hosts that never ask (dehu, the sede) made
// their pages fail now and then with an empty response.
const CERT_HOSTS = [
  'pasarela.clave.gob.es', 'pasarela-ident.clave.gob.es', 'se-pasarela.clave.gob.es',
  'afirma.redsara.es', 'se-afirma.redsara.es', 'autentica.redsara.es',
];
const CASE_ID = /^\d{8}\/\d{6}$/;

// ---------- pure parts (tested) ----------

function expandHome(p) {
  return p && p.startsWith('~/') ? path.join(process.env.HOME || '', p.slice(2)) : p;
}

function loadConfig(file) {
  const cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!cfg.state_dir) throw new Error('config: state_dir is required');
  if (!Array.isArray(cfg.people) || !cfg.people.length) throw new Error('config: people[] is required');
  cfg.state_dir = expandHome(cfg.state_dir);
  cfg.download_senders = (cfg.download_senders || []).map(s => s.toLowerCase());
  cfg.timeout_minutes = Number(cfg.timeout_minutes) || 25;
  for (const p of cfg.people) {
    if (!p.id || !/^[a-z0-9_-]+$/i.test(p.id)) throw new Error('config: every person needs an id of letters, digits, - or _');
    const a = p.auth || {};
    if (a.kind !== 'pkcs12' && a.kind !== 'chrome') throw new Error(`config ${p.id}: auth.kind must be pkcs12 or chrome`);
    for (const k of ['pfx', 'passphrase_file', 'chrome']) if (a[k]) a[k] = expandHome(a[k]);
    if (a.kind === 'pkcs12' && !a.pfx) throw new Error(`config ${p.id}: auth.pfx is required for pkcs12`);
    p.sede = p.sede !== false;
    p.sede_role = p.sede_role || 'Solicitante';
    p.sede_days = Number(p.sede_days) || 180;
    p.sede_slice_days = Math.min(Number(p.sede_slice_days) || 30, 366);
    if (!/^(Solicitante|Inscrito|Ambos)$/.test(p.sede_role)) throw new Error(`config ${p.id}: sede_role must be Solicitante, Inscrito or Ambos`);
    p.dehu = p.dehu !== false;
  }
  return cfg;
}

function clean(s) {
  return String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
}

// Rows of the «TUS EXPEDIENTES» table, as arrays of cell texts, into case objects keyed by id.
function parseCases(rows) {
  const cases = {};
  for (const r of rows) {
    const cells = r.map(clean);
    if (!CASE_ID.test(cells[0] || '')) continue;
    const [id, procedure, status, created, applicant, subject] = cells;
    cases[id] = { id, procedure, status, created, applicant, subject };
  }
  return cases;
}

const DEHU_ID = /^[0-9a-f]{16,}$/i;
const DEHU_ROW_ID = /^(?=.*\d)[A-Z0-9][A-Z0-9-]{5,}$/i;
// The three DEHú lists: snapshot key and the word the summary uses.
const DEHU_TABS = [['pending', 'pending'], ['done', 'accepted'], ['communications', 'communications']];
const DETAIL_LABELS = ['Concepto', 'Organismo raíz', 'Fecha de estado final', 'Fecha leída', 'Fecha de caducidad', 'Fecha caducidad', 'Fecha de expiración'];

// Where the notification id sits in a data row, or -1.
// Ids are not all hex: Catastro sends 14 digits («12345678901234»), the TGSS a letter and digits («A123456789»).
// So once the header is known the id is read from the «Identificador» column, lined up from the right
// (data rows carry extra empty cells in front), and the hex test is only the fallback before a header.
function dehuIdAt(cells, headers, headerId) {
  if (headerId >= 0 && headers.length && cells.length >= headers.length) {
    const at = headerId + (cells.length - headers.length);
    if (DEHU_ROW_ID.test(cells[at] || '')) return at;
  }
  return cells.findIndex(c => DEHU_ID.test(c));
}

// The rows of a DEHú result table (each an array of cell texts, header row included) into items.
// A data row starts with the notification id; the expandable row after it holds «Concepto … Organismo raíz …».
function parseDehuRows(rows) {
  let headers = [], headerId = -1;
  const items = [];
  for (const raw of rows) {
    const cells = raw.map(clean);
    if (cells.some(c => /^Identificador$/i.test(c))) {
      headers = cells.map(c => c.toLowerCase());
      headerId = headers.indexOf('identificador');
      continue;
    }
    const idAt = dehuIdAt(cells, headers, headerId);
    if (idAt >= 0) {
      const it = { id: cells[idAt], cells: cells.filter(Boolean) };
      // Data rows carry extra empty cells in front, so columns are lined up on the id, not on position.
      const shift = idAt - headerId;
      headers.forEach((h, k) => {
        const v = cells[k + shift];
        if (!v || k === headerId) return;
        if (/organismo emisor/.test(h)) it.sender = v;
        else if (/concepto/.test(h)) it.subject = v;
        else if (/caducidad|expira/.test(h)) it.expires = v;
        else if (/fecha disposici/.test(h)) it.date = v;
        else if (/^estado$/.test(h)) it.status = v;
        else if (/v[ií]nculo/.test(h)) it.link = v;
      });
      items.push(it);
      continue;
    }
    const last = items[items.length - 1];
    const text = cells.join(' ');
    if (!last || !/Concepto|Organismo ra[ií]z|Fecha/.test(text)) continue;
    // «Concepto X Organismo raíz Y Fecha leída Z» -> {Concepto: X, ...}
    const rx = new RegExp('(' + DETAIL_LABELS.join('|') + ')\\s+', 'g');
    const parts = text.split(rx).map(clean);
    for (let k = 1; k + 1 < parts.length; k += 2) {
      const label = parts[k], v = parts[k + 1];
      if (label === 'Concepto') last.subject = v;
      else if (label === 'Organismo raíz') last.root = v;
      else if (/caducidad|expiraci/.test(label)) last.expires = v;
      else last.final_date = v;
    }
  }
  return items;
}

// «19-10-2026» or «19/10/2026» -> «2026-10-19», for comparing dates as strings.
function isoDate(d) {
  const m = String(d || '').match(/(\d{2})[-/](\d{2})[-/](\d{4})/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : null;
}

function dmy(date) {
  const z = (n) => String(n).padStart(2, '0');
  return `${z(date.getDate())}/${z(date.getMonth() + 1)}/${date.getFullYear()}`;
}

function byId(list) {
  const m = {};
  for (const x of list || []) m[x.id] = x;
  return m;
}

// Compare two snapshots. Returns a list of {kind, ...} events; empty means no change.
function diff(prev, cur) {
  const ev = [];
  if (!prev) {
    ev.push({ kind: 'baseline' });
    for (const n of (cur.dehu && cur.dehu.pending) || []) ev.push({ kind: 'notification_pending', item: n });
    return ev;
  }
  if (cur.sede && prev.sede) {
    const a = prev.sede.cases || {}, b = cur.sede.cases || {};
    for (const id of Object.keys(b)) {
      if (!a[id]) ev.push({ kind: 'case_new', case: b[id] });
      else if (a[id].status !== b[id].status) ev.push({ kind: 'case_status', case: b[id], from: a[id].status });
    }
    // A case created before the search window just aged out of it; that is not a resolution.
    const since = isoDate(cur.sede.since);
    for (const id of Object.keys(a)) {
      if (b[id]) continue;
      const created = isoDate(a[id].created);
      if (since && created && created < since) continue;
      ev.push({ kind: 'case_gone', case: a[id] });
    }
  }
  if (cur.dehu && prev.dehu) {
    for (const [key, kind] of [['pending', 'notification_pending'], ['done', 'notification_done'], ['communications', 'communication_new']]) {
      const a = byId(prev.dehu[key]);
      for (const x of cur.dehu[key] || []) if (!a[x.id]) ev.push({ kind, item: x });
    }
  } else if (cur.dehu) {
    // The first DEHú read after polls that never read it: like the baseline, every pending
    // notification is reported.
    for (const n of cur.dehu.pending || []) ev.push({ kind: 'notification_pending', item: n });
  }
  return ev;
}

function senderAllowed(item, senders) {
  const s = clean([item.sender, item.subject, ...(item.cells || [])].join(' ')).toLowerCase();
  return senders.some(x => s.includes(x));
}

function describe(e) {
  const c = e.case, i = e.item;
  const it = i ? [i.sender, i.subject, i.date && `fecha ${i.date}`, i.expires && `caduca ${i.expires}`].filter(Boolean).join(' · ') || i.cells.join(' | ') : '';
  switch (e.kind) {
    case 'baseline': return 'First poll: this snapshot is the baseline.';
    case 'case_new': return `NEW CASE ${c.id} ${c.procedure} (${c.status}) ${c.subject}`;
    case 'case_status': return `CASE ${c.id} ${c.procedure}: ${e.from} -> ${c.status} (${c.subject})`;
    case 'case_gone': return `CASE GONE ${c.id} ${c.procedure} (${c.subject}), last status ${c.status}: resolved or refused, look for the reply`;
    case 'notification_pending': return `PENDING NOTIFICATION (not opened): ${it}`;
    case 'notification_done': return `New accepted notification: ${it}`;
    case 'communication_new': return `NEW COMMUNICATION: ${it}`;
    default: return JSON.stringify(e);
  }
}

// One DEHú tab as a summary phrase. A tab that was not read in full says so; it never shows a count
// it did not read.
function dehuTabPhrase(label, list, tab) {
  if (!tab || tab.state === 'read') return `${list.length} ${label}`;
  if (tab.state === 'not read') return `${label} NOT READ (${tab.note})`;
  return `${label} NOT FULLY READ (${tab.note})`;
}

// What this poll could not read: whole sides kept from an earlier poll, and DEHú tabs read short.
function notRead(snap) {
  const out = [];
  if (snap.sede_stale) out.push('sede');
  if (snap.dehu_stale) out.push('dehu');
  else if (snap.dehu) {
    for (const [key, label] of DEHU_TABS) {
      const t = (snap.dehu.tabs || {})[key];
      if (t && t.state !== 'read') out.push(`dehu ${label}`);
    }
  }
  return out;
}

function summarize(id, snap, events, errors) {
  const L = [`gwt-spanish-poller ${id} ${snap.polled_at}`];
  const kept = (side) => (snap[side] ? `the snapshot ${snap[side + '_read_at'] ? 'read at ' + snap[side + '_read_at'] : 'of an earlier poll'} is kept`
    : 'no earlier snapshot of it to keep');
  if (snap.sede_stale) {
    L.push(`sede: NOT READ this run; ${kept('sede')}`);
  } else if (snap.sede) {
    const n = Object.values(snap.sede.cases);
    const by = {};
    for (const c of n) by[c.status] = (by[c.status] || 0) + 1;
    L.push(`sede: ${n.length} cases (${Object.entries(by).map(([k, v]) => `${v} ${k}`).join(', ') || 'none'})`);
  }
  if (snap.dehu_stale) {
    L.push(`dehu: NOT READ this run; ${kept('dehu')}`);
  } else if (snap.dehu) {
    const d = snap.dehu, tabs = d.tabs || {};
    const fresh = (key) => d[key].length - ((tabs[key] && tabs[key].kept) || 0);
    L.push('dehu: ' + [['pending', 'pending notifications'], ['done', 'accepted'], ['communications', 'communications']]
      .map(([key, label]) => dehuTabPhrase(label, d[key].slice(0, fresh(key)), tabs[key])).join(', '));
    for (const w of d.warnings || []) L.push(`  warning: ${w}`);
    for (const n of d.pending.slice(0, fresh('pending'))) L.push(`  pending: ${[n.sender, n.subject, n.expires && 'caduca ' + n.expires].filter(Boolean).join(' · ') || n.cells.join(' | ')}`);
  }
  for (const f of snap.downloaded || []) L.push(`downloaded: ${f}`);
  for (const e of errors) L.push(`ERROR: ${e}`);
  const real = events.filter(e => e.kind !== 'baseline' || events.length === 1);
  const missing = notRead(snap);
  if (events.length) L.push('changes:');
  else L.push(missing.length ? 'no changes in what was read' : 'no changes');
  for (const e of real) L.push('  ' + describe(e));
  if (missing.length) L.push(`not read this run: ${missing.join(', ')}`);
  return L.join('\n') + '\n';
}

// ---------- browser ----------

function pw() {
  return require('playwright');
}

function killByArg(dir) {
  // Anything still running with this profile in its arguments is ours and is ended by PID.
  let pids = [];
  try { pids = execFileSync('pgrep', ['-f', '--', dir], { encoding: 'utf8' }).split('\n').filter(Boolean).map(Number); } catch (e) { return []; }
  for (const pid of pids) if (pid !== process.pid) try { process.kill(pid, 'SIGKILL'); } catch (e) { /* gone */ }
  return pids;
}

async function hideHeadless(context, page) {
  const s = await context.newCDPSession(page);
  const ua = (await s.send('Browser.getVersion')).userAgent.replace('HeadlessChrome', 'Chrome');
  await s.send('Network.setUserAgentOverride', { userAgent: ua, acceptLanguage: 'es-ES,es' });
  await context.setExtraHTTPHeaders({ 'Accept-Language': 'es-ES,es' });
}

// Every poll starts from an empty browser profile: logins are cheap, and cookies left by an earlier
// session made DEHú answer with empty responses.
function freshProfile() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'gwt-spanish-poller-'));
}

async function openPkcs12(auth) {
  const passphrase = auth.passphrase_file ? fs.readFileSync(auth.passphrase_file, 'utf8').trim()
    : (auth.passphrase_env ? process.env[auth.passphrase_env] || '' : '');
  const dir = freshProfile();
  const context = await pw().chromium.launchPersistentContext(dir, {
    headless: true, viewport: { width: 1280, height: 1000 }, locale: 'es-ES', acceptDownloads: true,
    args: ['--disable-blink-features=AutomationControlled'],
    clientCertificates: (auth.cert_hosts || CERT_HOSTS).map(h => ({ origin: 'https://' + h, pfxPath: auth.pfx, passphrase })),
  });
  const page = context.pages()[0] || await context.newPage();
  await hideHeadless(context, page);
  return { context, page, close: async () => { await context.close().catch(() => {}); killByArg(dir); fs.rmSync(dir, { recursive: true, force: true }); } };
}

// Chrome launched by its own binary (no Playwright flags, so the macOS keychain and the
// AutoSelectCertificateForUrls policy work), driven over CDP.
async function openChrome(auth) {
  const dir = freshProfile();
  const portFile = path.join(dir, 'DevToolsActivePort');
  fs.rmSync(portFile, { force: true });
  const bin = auth.chrome || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const child = spawn(bin, [`--user-data-dir=${dir}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    '--headless=new', '--no-first-run', '--no-default-browser-check', '--window-size=1280,1000', 'about:blank'],
  { stdio: 'ignore' });
  let port = null;
  for (let i = 0; i < 100 && !port; i++) {
    await new Promise(r => setTimeout(r, 200));
    try { port = fs.readFileSync(portFile, 'utf8').split('\n')[0].trim() || null; } catch (e) { /* not yet */ }
  }
  const close = async () => {
    try { process.kill(child.pid, 'SIGTERM'); } catch (e) { /* gone */ }
    await new Promise(r => setTimeout(r, 1500));
    try { process.kill(child.pid, 'SIGKILL'); } catch (e) { /* gone */ }
    killByArg(dir);
    fs.rmSync(dir, { recursive: true, force: true });
  };
  if (!port) { await close(); throw new Error('chrome did not open a DevTools port'); }
  const browser = await pw().chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  const context = browser.contexts()[0] || await browser.newContext();
  const page = context.pages()[0] || await context.newPage();
  await hideHeadless(context, page);
  return { context, page, close: async () => { await browser.close().catch(() => {}); await close(); } };
}

const wait = (page, ms) => page.waitForTimeout(ms);

// In pkcs12 mode Playwright routes traffic through its client-certificate proxy, and DEHú pages
// sometimes come back as ERR_EMPTY_RESPONSE through it. A second try a few seconds later works.
async function goto(page, url) {
  for (let i = 1; ; i++) {
    try { return await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 }); } catch (e) {
      if (i >= 4 || !/ERR_EMPTY_RESPONSE|ERR_CONNECTION_RESET|ERR_CONNECTION_CLOSED/.test(String(e))) throw e;
      await wait(page, 4000 * i);
    }
  }
}

// Pages where a login or a list failed are always saved, even without --dump: without them a
// failure that only happens on some days («login did not reach the inbox») cannot be diagnosed.
let errorDumpDir = null;
const setErrorDumpDir = dir => { errorDumpDir = dir; };

async function dump(page, dumpDir, name) {
  const dir = dumpDir || (/-err/.test(name) ? errorDumpDir : null);
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, name + '.html'), await page.content().catch(() => ''));
  fs.writeFileSync(path.join(dir, name + '.txt'), await page.innerText('body').catch(() => ''));
  await page.screenshot({ path: path.join(dir, name + '.png'), fullPage: true }).catch(() => {});
}

// Every table on the page as {headers, rows}; rows are arrays of cell texts.
async function tables(page) {
  return page.evaluate(() => [...document.querySelectorAll('table')].map(t => ({
    headers: [...t.querySelectorAll('thead th, tr:first-child th')].map(th => th.innerText),
    rows: [...t.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText)).filter(r => r.length),
  })));
}

async function pickRadio(page, nameHint, labelRx) {
  // Radios are chosen by their visible label, so a renamed id does not silently pick the wrong one.
  const id = await page.evaluate(([hint, src]) => {
    const rx = new RegExp(src, 'i');
    for (const r of document.querySelectorAll('input[type=radio]')) {
      if (!(r.name + ' ' + r.id).toLowerCase().includes(hint)) continue;
      const lab = (r.labels && r.labels[0] && r.labels[0].innerText) || (r.parentElement && r.parentElement.innerText) || '';
      if (rx.test(lab.trim())) return r.id;
    }
    return null;
  }, [nameHint, labelRx.source]);
  if (!id) throw new Error(`sede: no radio ${nameHint} labelled ${labelRx}`);
  await page.check(`input[id="${id}"]`, { force: true });
}

async function sedeForm(page, dumpDir) {
  await goto(page, SEDE_URL);
  // Either the form shows, or Cl@ve asks how to log in, or Cl@ve still holds a session and hands
  // straight back to the sede without asking.
  let clicked = false;
  for (let i = 0; i < 60; i++) {
    await wait(page, 1500);
    const body = await page.innerText('body').catch(() => '');
    if (/BUSCAR EXPEDIENTES/i.test(body)) break;
    if (!clicked && CLAVE_CERT_BUTTON.test(body)) {
      await page.getByText(CLAVE_CERT_BUTTON).first().click().catch(() => {});
      clicked = true;
    }
  }
  if (!/BUSCAR EXPEDIENTES/i.test(await page.innerText('body').catch(() => ''))) {
    await dump(page, dumpDir, 'sede-err-login');
    throw new Error('sede: login did not reach «Estado de mis expedientes»');
  }
}

// The sede sometimes answers a search with its own «Página no encontrada» page instead of the results
// (seen on several days, on the first or a later slice; the next poll usually reads fine). Name that
// case so the summary does not read like a parser fault.
function sedeNoCountError(body, from, to) {
  const range = `${dmy(from)}-${dmy(to)}`;
  if (/P[aá]gina no encontrada/i.test(body)) return `sede: the sede answered «Página no encontrada» to the search ${range}`;
  return `sede: search ${range} returned no result count`;
}

// One search over [from, to] (Date objects). Returns the cases, or null when the sede answers
// «demasiados registros» for that range.
async function sedeSearch(page, dumpDir, role, from, to) {
  // The results page repeats the search form, so later slices reuse it instead of a new Cl@ve round trip.
  if (!/BUSCAR EXPEDIENTES/i.test(await page.innerText('body').catch(() => ''))) await sedeForm(page, dumpDir);
  await pickRadio(page, 'tipobusqueda', new RegExp('^' + role + '$', 'i'));
  // Active and inactive: a refused case drops out of both, which is what case_gone catches.
  await pickRadio(page, 'situacion', /^Ambos$/);
  await page.fill('#fechaCreacionInicio', dmy(from));
  await page.fill('#fechaCreacionFin', dmy(to));
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.waitForNavigation({ timeout: 60000 }).catch(() => {}),
    page.getByRole('button', { name: 'Buscar' }).click(),
  ]);
  await wait(page, 1500);
  const body = await page.innerText('body');
  if (/demasiados registros/i.test(body)) return null;
  const found = body.match(/(\d+) expedientes? encontrados?/);
  if (!found) {
    if (/no se encuentran datos de expedientes|no se han encontrado/i.test(body)) return {};
    await dump(page, dumpDir, 'sede-err');
    throw new Error(sedeNoCountError(body, from, to));
  }
  const total = Number(found[1]);
  const cases = {};
  const tag = dmy(from).replace(/\//g, '');
  for (let p = 1; p <= 100 && Object.keys(cases).length < total; p++) {
    if (p > 1) {
      const next = page.getByText('Siguiente', { exact: true }).last();
      if (!(await next.count())) break;
      await Promise.all([page.waitForNavigation({ timeout: 60000 }).catch(() => {}), next.click()]);
      await wait(page, 1500);
    }
    await dump(page, dumpDir, `sede-${tag}-p${p}`);
    const before = Object.keys(cases).length;
    for (const t of await tables(page)) Object.assign(cases, parseCases(t.rows));
    if (p > 1 && Object.keys(cases).length === before) break;
  }
  if (Object.keys(cases).length !== total) throw new Error(`sede: ${dmy(from)}-${dmy(to)} says ${total} cases, parsed ${Object.keys(cases).length}`);
  return cases;
}

// The last `days` days, searched in slices; a slice that is still too big is split in half.
async function pollSede(page, dumpDir, role, days, sliceDays) {
  const DAY = 86400000;
  const today = new Date(new Date().toDateString());
  const start = new Date(today.getTime() - days * DAY);
  const queue = [];
  for (let t = today.getTime(); t > start.getTime(); t -= sliceDays * DAY) {
    queue.push([new Date(Math.max(start.getTime(), t - (sliceDays - 1) * DAY)), new Date(t)]);
  }
  const cases = {};
  while (queue.length) {
    const [from, to] = queue.shift();
    const got = await sedeSearch(page, dumpDir, role, from, to);
    if (got) { Object.assign(cases, got); continue; }
    const span = Math.round((to - from) / DAY);
    if (span < 1) throw new Error(`sede: too many cases on ${dmy(from)} alone`);
    const mid = new Date(from.getTime() + Math.floor(span / 2) * DAY);
    queue.unshift([from, mid], [new Date(mid.getTime() + DAY), to]);
  }
  return { since: dmy(start), total: Object.keys(cases).length, cases };
}

async function dehuLogin(page, dumpDir) {
  await goto(page, DEHU_URL);
  await wait(page, 4000);
  // The inbox home lists «Notificaciones pendientes»; the public landing page (same URL) does not.
  const inInbox = async () => /Notificaciones pendientes/.test(await page.innerText('body').catch(() => ''));
  let tries = 0, clicked = false;
  // Bounded by time, not rounds: one round can spend minutes in page reloads.
  const until = Date.now() + 6 * 60000;
  while (Date.now() < until && !(await inInbox())) {
    const body = await page.innerText('body').catch(() => '');
    // After many logins in a short time DEHú puts a CAPTCHA in front of the inbox. Only a person
    // can pass it; poll less often.
    if (/completa el CAPTCHA/i.test(body)) {
      await dump(page, dumpDir, 'dehu-err-captcha');
      throw new Error('dehu: CAPTCHA after login (too many logins lately); solve it in a browser or poll less often');
    }
    // An expired earlier session shows a modal with a single «Aceptar»; it only closes the message.
    const modal = page.locator('dnt-modal').getByText('Aceptar');
    if (await modal.count()) { await modal.first().click().catch(() => {}); await wait(page, 1500); continue; }
    if (/Acceder a DEH/.test(body) && tries < 5) {
      tries++;
      clicked = false;
      await page.locator('.dnt-button--primary-light').first().click().catch(() => {});
    } else if (!clicked && CLAVE_CERT_BUTTON.test(body)) {
      // Cl@ve may ask how to log in, or hand straight back when it still holds a session.
      await page.getByText(CLAVE_CERT_BUTTON).first().click().catch(() => {});
      clicked = true;
    } else if (/chrome-error:/.test(page.url()) || /ERR_EMPTY_RESPONSE/.test(body)) {
      // The hand-back sometimes answers an empty response; the session is set by then, so reload.
      // The Cl@ve certificate step also resets the connection now and then for a minute or two;
      // reloading lands on the public page and the next round logs in again.
      await wait(page, 10000);
      await goto(page, DEHU_HOME);
    }
    await wait(page, 1500);
  }
  if (!(await inInbox())) {
    await dump(page, dumpDir, 'dehu-err-login');
    throw new Error('dehu: login did not reach the inbox (' + page.url().split('?')[0] + ')');
  }
  await wait(page, 4000);
  await dump(page, dumpDir, 'dehu-0-home');
}

// Rows of every table inside the app, shadow roots included (DEHú is built from web components).
async function deepRows(page) {
  return page.evaluate(() => {
    const rows = [];
    let total = null;
    const visit = (root) => {
      for (const el of root.querySelectorAll('*')) {
        if (el.tagName === 'TR') rows.push([...el.children].map(c => (c.innerText || c.textContent || '')));
        if (el.tagName === 'DNT-PAGINATION-TOTAL') total = (el.innerText || el.textContent || '').trim();
        if (el.shadowRoot) visit(el.shadowRoot);
      }
    };
    visit(document);
    return { rows: rows.filter(r => !r.some(c => /^\s*[LMXJVSD]\s*$/.test(c))), total };
  });
}

// «loaded» when the table gave at least one notification row, «empty» when the page says there are none,
// «did not load» otherwise. Rows are what deepRows returns; the same check runs on a saved .rows.json.
function dehuLoadState(items, empty) {
  if (items.length) return 'loaded';
  return empty ? 'empty' : 'did not load';
}

const DEHU_PAGE_ROWS = 10;

// How completely one DEHú tab was read: {state, expected, rows, note}. state is «read», «partial» (some
// rows are missing) or «not read». `counter` is the number on the tab («Notificaciones pendientes 2»),
// `total` the list's own «1-10 de N elementos» line. The tab counter wins when both are known: it is
// the number the person sees. Only the first page of a list is read.
function dehuTabRead(items, { counter = null, total = null, empty = false } = {}) {
  const m = String(total || '').match(/de (\d+) elementos/);
  const listed = m ? Number(m[1]) : null;
  const expected = counter != null ? counter : listed;
  const rows = items.length;
  const out = (state, note, paged) => ({ state, expected, rows, note, ...(paged ? { paged } : {}) });
  if (!rows) {
    if (expected === 0 || (expected == null && dehuLoadState(items, empty) === 'empty')) return out('read', null);
    return out('not read', expected ? `tab counter says ${expected}, the list did not load` : 'the list did not load');
  }
  if (expected == null || rows >= expected) return out('read', null);
  if (rows < Math.min(expected, DEHU_PAGE_ROWS)) return out('partial', `tab counter says ${expected}, ${rows} rows read`);
  // A full first page and more behind it: the page was read, the later pages were not.
  return out('partial', `${expected - rows} more on later pages, not read`, true);
}

async function dehuList(page, dumpDir, name, counter) {
  // A single-page app: wait until the table shows every row the counter promises (one page at most),
  // or the page says the list is empty.
  let got = { rows: [], total: null }, items = [], read = null;
  for (let i = 0; i < 30; i++) {
    await wait(page, 1500);
    got = await deepRows(page);
    items = parseDehuRows(got.rows);
    const empty = /no dispones/i.test(await page.innerText('body').catch(() => ''));
    read = dehuTabRead(items, { counter, total: got.total, empty });
    if (read.state === 'read' || read.paged) break;
  }
  await dump(page, dumpDir, name);
  if (dumpDir) fs.writeFileSync(path.join(dumpDir, name + '.rows.json'), JSON.stringify(got, null, 1));
  // A list that did not match its counter is saved even without --dump, so the next time it happens
  // the page is there to look at. The rows sit in shadow roots that the .html and .txt do not hold, so
  // what deepRows returned is saved next to them.
  if (read.state !== 'read' && !read.paged) {
    await dump(page, null, name + '-err');
    if (errorDumpDir) {
      fs.mkdirSync(errorDumpDir, { recursive: true });
      fs.writeFileSync(path.join(errorDumpDir, name + '-err.rows.json'), JSON.stringify({ counter, read: { ...read }, ...got }, null, 1));
    }
  }
  return { items, ...read };
}

async function dehuCounters(page) {
  const t = await page.innerText('body');
  const n = (rx) => { const m = t.match(rx); return m ? Number(m[1]) : null; };
  return { pending: n(/Notificaciones pendientes\s*\n\s*(\d+)/), done: n(/Notificaciones realizadas\s*\n\s*(\d+)/) };
}

// Read the three DEHú lists. One list that fails or reads short never throws the others away: each
// tab carries its own {state, expected, rows, note} in `tabs`.
async function pollDehu(page, dumpDir) {
  await dehuLogin(page, dumpDir);
  let counters = { pending: null, done: null };
  for (let attempt = 0; attempt < 2 && counters.pending == null; attempt++) {
    // The click on the tab sometimes lands on a connection-reset page; one reload of the home fixes it.
    if (attempt) { await goto(page, DEHU_HOME); await wait(page, 4000); }
    await page.getByText('Notificaciones pendientes', { exact: true }).first().click().catch(() => {});
    for (let i = 0; i < 20 && counters.pending == null; i++) { await wait(page, 1000); counters = await dehuCounters(page).catch(() => counters); }
  }
  if (counters.pending == null) {
    await dump(page, dumpDir, 'dehu-err-counters');
    throw new Error('dehu: notification counters not found');
  }
  const lists = {};
  const read = async (key, name, open, counter) => {
    try {
      await open();
      lists[key] = await dehuList(page, dumpDir, name, counter);
    } catch (e) {
      lists[key] = { items: [], state: 'not read', expected: counter, rows: 0, note: String(e.message || e) };
    }
  };
  // Listing only: the pending rows are read, never clicked, so nothing is accepted or rejected.
  await read('pending', 'dehu-1-pending', async () => {}, counters.pending);
  await read('done', 'dehu-2-done', () => page.locator('#pane-button-1').click({ timeout: 15000 }), counters.done);
  await read('communications', 'dehu-3-communications', async () => {
    await goto(page, DEHU_HOME);
    await wait(page, 4000);
    await page.getByText('Comunicaciones', { exact: true }).first().click();
  }, null);
  const out = { counters, tabs: {}, warnings: [] };
  for (const [key] of DEHU_TABS) {
    const { items, ...state } = lists[key];
    out[key] = [...items];
    out.tabs[key] = state;
  }
  return out;
}

// A tab that was not read in full keeps the previous snapshot's rows next to the ones it did read, so
// the next poll does not report them as new. Returns a new dehu snapshot.
function keepUnreadRows(prevDehu, dehu) {
  const out = { ...dehu };
  for (const [key] of DEHU_TABS) {
    const t = (dehu.tabs || {})[key];
    if (!t || t.state === 'read' || !prevDehu) continue;
    const seen = new Set((dehu[key] || []).map(x => x.id));
    out[key] = [...(dehu[key] || []), ...(prevDehu[key] || []).filter(x => !seen.has(x.id))];
    out.tabs = { ...out.tabs, [key]: { ...t, kept: out[key].length - (dehu[key] || []).length } };
  }
  return out;
}

// Centre of the first element matching `rx` by its own text, searched through shadow roots. With
// `rowId`, only inside the table row that holds that id. DEHú buttons sit in closed components, so
// they are clicked with the mouse at that point rather than through a locator.
async function deepPoint(page, rx, rowId) {
  return page.evaluate(([src, rowId]) => {
    const rx = new RegExp(src);
    document.querySelectorAll('app-chat-bot-view').forEach(e => e.remove());
    let hit = null;
    const visit = (root) => {
      for (const el of root.querySelectorAll('*')) {
        if (hit) return;
        const t = (el.textContent || '').trim();
        if (rx.test(t) && ![...el.children].some(c => rx.test((c.textContent || '').trim()))) hit = el;
        if (el.shadowRoot) visit(el.shadowRoot);
      }
    };
    if (rowId) {
      const rows = [];
      const collect = (root) => { for (const el of root.querySelectorAll('*')) { if (el.tagName === 'TR' && (el.innerText || '').includes(rowId)) rows.push(el); if (el.shadowRoot) collect(el.shadowRoot); } };
      collect(document);
      for (const r of rows) { visit(r); if (hit) break; }
    } else visit(document);
    if (!hit) return null;
    hit.scrollIntoView({ block: 'center' });
    const b = hit.getBoundingClientRect();
    return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  }, [rx.source, rowId || null]);
}

// Download the document of each readable communication from an allow-listed sender, once each.
// Only the communications list is touched; a notification is never opened.
async function downloadCommunications(page, items, senders, dir, already, notes) {
  const got = [];
  for (const it of items) {
    if (already.includes(it.id) || !senderAllowed(it, senders)) continue;
    await goto(page, DEHU_HOME);
    await wait(page, 3000);
    await page.getByText('Comunicaciones', { exact: true }).first().click();
    let pt = null;
    for (let i = 0; i < 30 && !pt; i++) { await wait(page, 1000); pt = await deepPoint(page, /^Detalles?$/, it.id); }
    if (!pt) { notes.push(`${it.id}: Detalles not found, not downloaded`); continue; }
    await page.mouse.click(pt.x, pt.y);
    await page.waitForURL(/communication-detail/, { timeout: 30000 });
    let ver = null;
    for (let i = 0; i < 20 && !ver; i++) { await wait(page, 1000); ver = await deepPoint(page, /^Ver documentos$/); }
    if (!ver) { notes.push(`${it.id}: «Ver documentos» not found, not downloaded`); continue; }
    const docRx = /\/api\/v1\/communications\/[^/]+\/document$/;
    const annexRx = /\/api\/v1\/communications\/[^/]+\/annexe$/;
    const docP = page.waitForResponse(r => docRx.test(r.url()), { timeout: 90000 });
    const annexP = page.waitForResponse(r => annexRx.test(r.url()), { timeout: 90000 }).catch(() => null);
    await page.mouse.click(ver.x, ver.y);
    const doc = await (await docP).json();
    if (!doc.content) { notes.push(`${it.id}: document came back empty`); continue; }
    fs.mkdirSync(dir, { recursive: true });
    const ext = /pdf/.test(doc.mimeType || '') ? '.pdf' : path.extname(doc.name || '') || '.bin';
    const file = path.join(dir, `${it.date ? isoDate(it.date) + '-' : ''}${it.id}${ext}`);
    fs.writeFileSync(file, Buffer.from(doc.content, 'base64'));
    got.push(file);
    already.push(it.id);
    const annex = await annexP;
    const n = annex ? ((await annex.json().catch(() => ({}))).annexes || []).length : 0;
    if (n) notes.push(`${it.id}: ${n} annexes not downloaded, open it in DEHú`);
  }
  return got;
}

// When a kept side of a snapshot was last read for real (snapshots older than this field: their poll
// time, unless that poll had already kept it from an earlier one). A null field means unknown.
function readAt(snap, side) {
  if ((side + '_read_at') in snap) return snap[side + '_read_at'];
  return snap[side + '_stale'] ? null : snap.polled_at;
}

// A list that did not load, or showed fewer rows than its counter, is an error (exit 2 when nothing
// else changed). It is reported next to the lists that did read, never instead of them. A list longer
// than one page is not an error: its first page was read, and the summary says the rest was not.
function dehuTabErrors(tabs) {
  const errs = [];
  for (const [key, label] of DEHU_TABS) {
    const t = (tabs || {})[key];
    if (!t || t.state === 'read' || t.paged) continue;
    errs.push(`dehu: ${label} ${t.state === 'not read' ? 'not read' : 'not fully read'}: ${t.note}`);
  }
  return errs;
}

async function pollPerson(cfg, person, dumpDir) {
  const stateFile = path.join(cfg.state_dir, person.id + '.json');
  const prev = fs.existsSync(stateFile) ? JSON.parse(fs.readFileSync(stateFile, 'utf8')) : null;
  const snap = { id: person.id, polled_at: new Date().toISOString(), downloaded: [] };
  const errors = [];
  const open = () => (person.auth.kind === 'pkcs12' ? openPkcs12(person.auth) : openChrome(person.auth));
  let session = null;
  // A stuck page must not leave a browser behind: a signal or the time budget closes it first.
  const onSignal = () => { (session ? session.close() : Promise.resolve()).finally(() => process.exit(EXIT_ERROR)); };
  process.once('SIGTERM', onSignal);
  process.once('SIGINT', onSignal);
  const budget = setTimeout(() => {
    console.error(`gwt-spanish-poller ${person.id}: over ${cfg.timeout_minutes} minutes, giving up`);
    onSignal();
  }, cfg.timeout_minutes * 60000);
  try {
    const d = dumpDir && path.join(dumpDir, person.id);
    setErrorDumpDir(path.join(cfg.state_dir, person.id + '-errors', snap.polled_at.replace(/[:.]/g, '-')));
    if (person.sede) try {
      session = await open();
      snap.sede = await pollSede(session.page, d, person.sede_role, person.sede_days, person.sede_slice_days);
      snap.sede_read_at = snap.polled_at;
    } catch (e) { errors.push(String(e.message || e)); }
    // DEHú gets its own empty browser: with the Cl@ve session the sede left behind, the DEHú login
    // in chrome mode kept landing back on the public page.
    if (session) { await session.close(); session = null; }
    if (person.dehu) try {
      session = await open();
      snap.dehu = await pollDehu(session.page, d);
      // Only rows read in this run are downloaded; rows kept from an earlier poll are not on the page.
      const already = (prev && prev.downloaded_ids) || [];
      if (cfg.download_senders.length) {
        snap.downloaded = await downloadCommunications(session.page, snap.dehu.communications, cfg.download_senders,
          path.join(cfg.state_dir, person.id + '-downloads'), already, snap.dehu.warnings);
      }
      snap.downloaded_ids = already;
    } catch (e) { errors.push(String(e.message || e)); }
  } finally {
    clearTimeout(budget);
    process.removeListener('SIGTERM', onSignal);
    process.removeListener('SIGINT', onSignal);
    if (session) await session.close();
  }
  if (errors.length && errorDumpDir && fs.existsSync(errorDumpDir)) errors.push('failed pages saved in ' + errorDumpDir);
  const r = finishPoll(person, prev, snap, errors);
  fs.mkdirSync(cfg.state_dir, { recursive: true });
  if (prev) fs.writeFileSync(path.join(cfg.state_dir, person.id + '.prev.json'), JSON.stringify(prev, null, 1));
  fs.writeFileSync(stateFile, JSON.stringify({ ...r.snap, events: r.events }, null, 1));
  fs.writeFileSync(path.join(cfg.state_dir, person.id + '.txt'), r.text);
  return { text: r.text, changed: r.changed, failed: r.failed };
}

// Everything after the browser: merge what was read with the previous snapshot, decide what was not
// read, diff and summarize. `snap` holds the sides read in this run (sede, and dehu as pollDehu returned
// it); a side missing from it failed. Returns {snap, events, text, changed, failed}.
function finishPoll(person, prev, snap, errors) {
  snap = { ...snap };
  errors = [...errors];
  const prevDehu = prev && prev.dehu;
  if (snap.dehu) {
    snap.dehu = keepUnreadRows(prevDehu, snap.dehu);
    errors.push(...dehuTabErrors(snap.dehu.tabs));
    // The pending list is the one someone acts on, so the read time is the time it was last read
    // (null when it never was).
    const pending = (snap.dehu.tabs || {}).pending;
    snap.dehu_read_at = !pending || pending.state === 'read' ? snap.polled_at : prevDehu ? readAt(prev, 'dehu') : null;
  }
  // A failed part keeps its previous snapshot, so a broken login never reads as «every case disappeared».
  // The summary says so and names when the kept snapshot was read. A part that failed with no earlier
  // snapshot to keep is still marked stale, so the summary never ends in a plain «no changes».
  if (person.sede !== false && !snap.sede) {
    Object.assign(snap, { sede_stale: true }, prev && prev.sede ? { sede: prev.sede, sede_read_at: readAt(prev, 'sede') } : {});
  }
  if (person.dehu !== false && !snap.dehu) {
    Object.assign(snap, { dehu_stale: true }, prevDehu ? { dehu: prevDehu, dehu_read_at: readAt(prev, 'dehu') } : {});
  }
  if (!snap.downloaded_ids) snap.downloaded_ids = (prev && prev.downloaded_ids) || [];
  const events = diff(prev, snap).filter(e => !(snap.sede_stale && /^case_/.test(e.kind)) && !(snap.dehu_stale && /notification|communication/.test(e.kind)));
  const text = summarize(person.id, snap, events, errors);
  return { snap, events, text, changed: events.length > 0, failed: errors.length > 0 };
}

function parseArgs(argv) {
  const a = { config: process.env.GWT_SPANISH_POLLER_CONFIG, only: null, dump: null };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--config') a.config = argv[++i];
    else if (k === '--only') a.only = argv[++i];
    else if (k === '--dump') a.dump = argv[++i];
    else if (k === '-h' || k === '--help') a.help = true;
    else throw new Error('unknown argument ' + k);
  }
  return a;
}

async function main(argv) {
  let args;
  try { args = parseArgs(argv); } catch (e) { console.error(e.message); return EXIT_ERROR; }
  if (args.help || !args.config) {
    console.error('usage: gwt-spanish-poller --config FILE [--only ID] [--dump DIR]   (or set GWT_SPANISH_POLLER_CONFIG)');
    return args.help ? EXIT_SAME : EXIT_ERROR;
  }
  const cfg = loadConfig(expandHome(args.config));
  let changed = false, failed = false;
  for (const p of cfg.people) {
    if (args.only && p.id !== args.only) continue;
    try {
      const r = await pollPerson(cfg, p, args.dump && expandHome(args.dump));
      process.stdout.write(r.text);
      changed = changed || r.changed;
      failed = failed || r.failed;
    } catch (e) {
      console.error(`gwt-spanish-poller ${p.id}: ${e.stack || e}`);
      failed = true;
    }
  }
  return changed ? EXIT_CHANGED : failed ? EXIT_ERROR : EXIT_SAME;
}

module.exports = { dump, setErrorDumpDir, isoDate, openPkcs12, openChrome, dehuLogin, pollDehu, loadConfig, parseCases, parseDehuRows, dehuLoadState, dehuTabRead, dehuTabErrors, keepUnreadRows, readAt, finishPoll, dehuList, sedeNoCountError, diff, senderAllowed, summarize, describe, main, EXIT_SAME, EXIT_CHANGED, EXIT_ERROR };
