'use strict';
/*
gwt-poller: watch Spanish civil-registry requests (sede.mjusticia «Estado de mis expedientes») and the
DEHú inbox for one or more people, each logging in with their own client certificate.

  gwt-poller [--config FILE] [--only ID] [--dump DIR]

Per person it snapshots the case list and the DEHú notifications and communications, diffs them against
the previous snapshot, writes <state_dir>/<id>.json and <state_dir>/<id>.txt, and prints the summary.
Exit 0: nothing changed. Exit 10: something changed. Exit 2: a login or a page failed and nothing
else changed (the failed part keeps its previous snapshot).

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
const DETAIL_LABELS = ['Concepto', 'Organismo raíz', 'Fecha de estado final', 'Fecha leída', 'Fecha de caducidad', 'Fecha caducidad', 'Fecha de expiración'];

// The rows of a DEHú result table (each an array of cell texts, header row included) into items.
// A data row starts with the notification id; the expandable row after it holds «Concepto … Organismo raíz …».
function parseDehuRows(rows) {
  let headers = [], headerId = 0;
  const items = [];
  for (const raw of rows) {
    const cells = raw.map(clean);
    const idAt = cells.findIndex(c => DEHU_ID.test(c));
    if (cells.some(c => /^Identificador$/i.test(c))) {
      headers = cells.map(c => c.toLowerCase());
      headerId = headers.indexOf('identificador');
      continue;
    }
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

function summarize(id, snap, events, errors) {
  const L = [`gwt-poller ${id} ${snap.polled_at}`];
  if (snap.sede) {
    const n = Object.values(snap.sede.cases);
    const by = {};
    for (const c of n) by[c.status] = (by[c.status] || 0) + 1;
    L.push(`sede: ${n.length} cases (${Object.entries(by).map(([k, v]) => `${v} ${k}`).join(', ') || 'none'})`);
  }
  if (snap.dehu) {
    const d = snap.dehu;
    L.push(`dehu: ${d.pending.length} pending notifications, ${d.done.length} accepted, ${d.communications.length} communications`);
    for (const w of d.warnings || []) L.push(`  warning: ${w}`);
    for (const n of d.pending) L.push(`  pending: ${[n.sender, n.subject, n.expires && 'caduca ' + n.expires].filter(Boolean).join(' · ') || n.cells.join(' | ')}`);
  }
  for (const f of snap.downloaded || []) L.push(`downloaded: ${f}`);
  for (const e of errors) L.push(`ERROR: ${e}`);
  const real = events.filter(e => e.kind !== 'baseline' || events.length === 1);
  L.push(events.length ? 'changes:' : 'no changes');
  for (const e of real) L.push('  ' + describe(e));
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
  return fs.mkdtempSync(path.join(os.tmpdir(), 'gwt-poller-'));
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

async function dump(page, dumpDir, name) {
  if (!dumpDir) return;
  fs.mkdirSync(dumpDir, { recursive: true });
  fs.writeFileSync(path.join(dumpDir, name + '.html'), await page.content().catch(() => ''));
  fs.writeFileSync(path.join(dumpDir, name + '.txt'), await page.innerText('body').catch(() => ''));
  await page.screenshot({ path: path.join(dumpDir, name + '.png'), fullPage: true }).catch(() => {});
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
    throw new Error('sede: search returned no result count');
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

async function dehuList(page, dumpDir, name, expected) {
  // A single-page app: wait until the table shows rows, or the page says the list is empty.
  let got = { rows: [], total: null }, items = [], empty = false;
  for (let i = 0; i < 30; i++) {
    await wait(page, 1500);
    got = await deepRows(page);
    items = parseDehuRows(got.rows);
    empty = /no dispones/i.test(await page.innerText('body').catch(() => ''));
    if (items.length && (expected == null || items.length >= Math.min(expected, 10))) break;
    if (empty && !items.length) break;
  }
  await dump(page, dumpDir, name);
  if (dumpDir) fs.writeFileSync(path.join(dumpDir, name + '.rows.json'), JSON.stringify(got, null, 1));
  if (!items.length && !empty) throw new Error(`dehu: ${name} did not load`);
  if (expected != null && items.length < Math.min(expected, 10)) throw new Error(`dehu: ${name} counter says ${expected}, parsed ${items.length}`);
  // «1-10 de 14 elementos»: only the first page is read; say so rather than drop the rest silently.
  const m = String(got.total || '').match(/de (\d+) elementos/);
  const total = m ? Number(m[1]) : expected;
  if (total != null && total > items.length) items.more = total - items.length;
  return items;
}

async function dehuCounters(page) {
  const t = await page.innerText('body');
  const n = (rx) => { const m = t.match(rx); return m ? Number(m[1]) : null; };
  return { pending: n(/Notificaciones pendientes\s*\n\s*(\d+)/), done: n(/Notificaciones realizadas\s*\n\s*(\d+)/) };
}

async function pollDehu(page, dumpDir) {
  await dehuLogin(page, dumpDir);
  await page.getByText('Notificaciones pendientes', { exact: true }).first().click();
  let counters = { pending: null, done: null };
  for (let i = 0; i < 20 && counters.pending == null; i++) { await wait(page, 1000); counters = await dehuCounters(page); }
  if (counters.pending == null) throw new Error('dehu: notification counters not found');
  // Listing only: the pending rows are read, never clicked, so nothing is accepted or rejected.
  const pending = await dehuList(page, dumpDir, 'dehu-1-pending', counters.pending);
  await page.locator('#pane-button-1').click({ timeout: 15000 });
  const done = await dehuList(page, dumpDir, 'dehu-2-done', counters.done);
  await goto(page, DEHU_HOME);
  await wait(page, 4000);
  await page.getByText('Comunicaciones', { exact: true }).first().click();
  const communications = await dehuList(page, dumpDir, 'dehu-3-communications', null);
  const warnings = [['pending', pending], ['done', done], ['communications', communications]]
    .filter(([, l]) => l.more).map(([k, l]) => `${k}: ${l.more} more on later pages, not read`);
  return { pending: [...pending], done: [...done], communications: [...communications], warnings };
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
    console.error(`gwt-poller ${person.id}: over ${cfg.timeout_minutes} minutes, giving up`);
    onSignal();
  }, cfg.timeout_minutes * 60000);
  try {
    const d = dumpDir && path.join(dumpDir, person.id);
    if (person.sede) try {
      session = await open();
      snap.sede = await pollSede(session.page, d, person.sede_role, person.sede_days, person.sede_slice_days);
    } catch (e) { errors.push(String(e.message || e)); }
    // DEHú gets its own empty browser: with the Cl@ve session the sede left behind, the DEHú login
    // in chrome mode kept landing back on the public page.
    if (session) { await session.close(); session = null; }
    if (person.dehu) try {
      session = await open();
      snap.dehu = await pollDehu(session.page, d);
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
  // A failed part keeps its previous snapshot, so a broken login never reads as «every case disappeared».
  if (!snap.sede && prev && prev.sede) snap.sede_stale = true, snap.sede = prev.sede;
  if (!snap.dehu && prev && prev.dehu) snap.dehu_stale = true, snap.dehu = prev.dehu;
  if (!snap.downloaded_ids) snap.downloaded_ids = (prev && prev.downloaded_ids) || [];
  const events = diff(prev, snap).filter(e => !(snap.sede_stale && /^case_/.test(e.kind)) && !(snap.dehu_stale && /notification|communication/.test(e.kind)));
  const text = summarize(person.id, snap, events, errors);
  fs.mkdirSync(cfg.state_dir, { recursive: true });
  if (prev) fs.writeFileSync(path.join(cfg.state_dir, person.id + '.prev.json'), JSON.stringify(prev, null, 1));
  fs.writeFileSync(stateFile, JSON.stringify({ ...snap, events }, null, 1));
  fs.writeFileSync(path.join(cfg.state_dir, person.id + '.txt'), text);
  return { text, changed: events.length > 0, failed: errors.length > 0 };
}

function parseArgs(argv) {
  const a = { config: process.env.GWT_POLLER_CONFIG, only: null, dump: null };
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
    console.error('usage: gwt-poller --config FILE [--only ID] [--dump DIR]   (or set GWT_POLLER_CONFIG)');
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
      console.error(`gwt-poller ${p.id}: ${e.stack || e}`);
      failed = true;
    }
  }
  return changed ? EXIT_CHANGED : failed ? EXIT_ERROR : EXIT_SAME;
}

module.exports = { isoDate, openPkcs12, openChrome, dehuLogin, loadConfig, parseCases, parseDehuRows, diff, senderAllowed, summarize, describe, main, EXIT_SAME, EXIT_CHANGED, EXIT_ERROR };
