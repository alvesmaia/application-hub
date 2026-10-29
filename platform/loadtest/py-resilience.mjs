// F7a — zero downtime and chaos against the Python stack (`app-hub-*`, hub on :8090). Only touches `app-hub-*`; does not touch `hub-*` nor `apphub-devtunnel`:
//
//   HUB=http://localhost:8090 HUB_USER=… HUB_PASSWORD=… node loadtest/py-resilience.mjs zd|chaos [--rps 60] [--out arq.md]
//
// zd    constant load (GET /_frag/ativos/{dashboard,assets?q=…} through the hub) while: Aplicar (blue/green) ×2,
//       deployment of a new version to production (import → approve) and Rollback; counts non-2xx responses.
// chaos constant load on servicos + ativos while: `docker kill app-hub-app-servicos` ×2, `docker restart
//       app-hub-web` ×1 and `docker restart app-hub-app-db` ×1; measures the time until it answers 200 again and checks that the database
//       of servicos lost no rows.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const MODE = process.argv[2];
if (!['zd', 'chaos'].includes(MODE)) { console.error('uso: py-resilience.mjs zd|chaos'); process.exit(2); }
const HUB = process.env.HUB || 'http://localhost:8090';
const USER = process.env.HUB_USER || 'admin@demo.local';
const PASS = process.env.HUB_PASSWORD || process.env.HUB_DEMO_PASSWORD || 'admin';
const arg = (n, d) => { const i = process.argv.indexOf(n); return i > 0 ? process.argv[i + 1] : d; };
const RPS = Number(arg('--rps', 60));
const here = path.dirname(fileURLToPath(import.meta.url));
const t0 = Date.now();
const lines = [];
const log = (m) => { const l = `${((Date.now() - t0) / 1000).toFixed(1)} s — ${m}`; lines.push(l); console.log(l); };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const docker = (...a) => execFileSync('docker', a, { encoding: 'utf8', env: { ...process.env, MSYS_NO_PATHCONV: '1' } }).trim();
// Container prefix (E2E_CONTAINER_PREFIX; isolated instance: app-hub-iso-, apps database in E2E_APP_DB).
const CP = process.env.E2E_CONTAINER_PREFIX || 'app-hub-';
const APPDB = process.env.E2E_APP_DB || `${CP}app-db`;
const own = (n) => { if (!/^app-hub-/.test(n)) throw new Error(`fora de app-hub-*: ${n}`); return n; };

let cookie = '';
async function login() {
  const r = await fetch(`${HUB}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: HUB },
    body: new URLSearchParams({ email: USER, password: PASS }),
  });
  cookie = (r.headers.get('set-cookie') || '').match(/hub_session=[^;]+/)[0];
}
async function api(method, url, json, extra = {}) {
  const r = await fetch(`${HUB}/api/ops${url}`, {
    method, headers: { cookie, origin: HUB, 'content-type': 'application/json', ...extra }, body: json ? JSON.stringify(json) : undefined,
  });
  const text = await r.text();
  return { status: r.status, json: text ? JSON.parse(text) : null };
}

// ---- load generator: RPS requests/s, 1 s windows ---------------------------------------
const URLS = MODE === 'zd'
  ? ['/_frag/ativos/dashboard', '/_frag/ativos/assets?q=NT-1']
  : ['/_frag/servicos/dashboard', '/_frag/ativos/dashboard'];
const buckets = new Map(); // second → {n, ok, bad:[status]}
let stop = false;
async function worker(i) {
  const gap = 1000 / (RPS / 8);
  for (let k = i; !stop; k++) {
    const t = Date.now();
    let st = 0;
    try { st = (await fetch(`${HUB}${URLS[k % URLS.length]}`, { headers: { cookie }, signal: AbortSignal.timeout(15000) })).status; } catch { st = 0; }
    const s = Math.floor((t - t0) / 1000);
    const b = buckets.get(s) || { n: 0, ok: 0, bad: {} };
    b.n++; if (st >= 200 && st < 300) b.ok++; else b.bad[st] = (b.bad[st] || 0) + 1;
    buckets.set(s, b);
    const left = gap - (Date.now() - t);
    if (left > 0) await wait(left);
  }
}
function window(fromMs, toMs) {
  let n = 0, ok = 0; const bad = {};
  for (const [s, b] of buckets) if (s * 1000 + t0 >= fromMs - 1000 && s * 1000 + t0 <= toMs) { n += b.n; ok += b.ok; for (const [k, v] of Object.entries(b.bad)) bad[k] = (bad[k] || 0) + v; }
  return { n, ok, bad };
}

await login();
log(`login ok · carga ${RPS} req/s em ${URLS.join(', ')}`);
const workers = Array.from({ length: 8 }, (_, i) => worker(i));
await wait(5000);
const base = window(t0, Date.now());
log(`linha de base: ${base.n} req, ${base.ok} 2xx, não-2xx ${JSON.stringify(base.bad)}`);
const rows = [];
const phase = async (name, fn) => {
  const a = Date.now();
  let err = '';
  try { await fn(); } catch (e) { err = String(e.message || e).slice(0, 200); }
  await wait(3000); // tail: in-flight responses
  const w = window(a, Date.now());
  rows.push({ name, secs: ((Date.now() - a) / 1000).toFixed(1), ...w, err });
  log(`${name}: ${w.n} req em ${((Date.now() - a) / 1000).toFixed(1)} s · 2xx ${w.ok} · não-2xx ${JSON.stringify(w.bad)}${err ? ` · ERRO: ${err}` : ''}`);
};

async function job(id) { for (let i = 0; i < 300; i++) { const j = (await api('GET', `/jobs/${id}`)).json; if (j.done) return j; await wait(1000); } throw new Error('job não terminou'); }

if (MODE === 'zd') {
  for (let i = 1; i <= 2; i++) {
    await phase(`Aplicar (blue/green) #${i}`, async () => {
      const r = await api('POST', '/runtime/ativos/apply?env=producao', {});
      if (r.status !== 200) throw new Error(`apply HTTP ${r.status} ${JSON.stringify(r.json)}`);
    });
  }
  // New version deployment: reuses the current container's image (same image, new version in the manifest).
  const hubBin = process.env.HUB_BIN;
  if (hubBin) {
    const id = docker('inspect', '-f', '{{.Image}}', own(`${CP}app-ativos`));
    const image = `${process.env.ZD_IMAGE_NAME || 'hub-ativos'}@${id}`;
    const dir = fs.mkdtempSync(path.join(process.env.TMPDIR || '/tmp', 'py-zd-'));
    const src = path.join(here, '..', 'samples', 'src', 'ativos');
    fs.cpSync(src, dir, { recursive: true });
    const v = `2.9.${Math.floor(Date.now() / 1000) % 100000}`;
    const mf = path.join(dir, 'manifest.json');
    const m = JSON.parse(fs.readFileSync(mf, 'utf8')); m.version = v; fs.writeFileSync(mf, JSON.stringify(m));
    const pkg = path.join(dir, 'zd.hubapp');
    execFileSync(hubBin, ['pack', '--dir', dir, '--image', image, '--out', pkg], { stdio: 'ignore' });
    let depId = 0;
    await phase(`Importar ${v} em produção + Aprovar (blue/green)`, async () => {
      const fd = new FormData();
      fd.append('package', new Blob([fs.readFileSync(pkg)], { type: 'application/zip' }), 'zd.hubapp');
      const r = await fetch(`${HUB}/api/ops/packages?env=producao&wait=true`, { method: 'POST', headers: { cookie, origin: HUB, 'x-hub-request': '1' }, body: fd });
      const body = await r.json();
      if (r.status !== 201 && r.status !== 200) throw new Error(`import HTTP ${r.status} ${JSON.stringify(body).slice(0, 150)}`);
      depId = (body.result?.deployment || body.deployment || body).id;
      const a = await api('POST', `/deployments/${depId}/approve?wait=true`, {});
      if (a.status !== 200 || a.json.status !== 'Implantado') throw new Error(`approve HTTP ${a.status} ${JSON.stringify(a.json).slice(0, 150)}`);
    });
    await phase('Rollback (blue/green)', async () => {
      const r = await api('POST', `/deployments/${depId}/rollback?wait=true`, {});
      if (r.status !== 200 || r.json.status !== 'Implantado') throw new Error(`rollback HTTP ${r.status} ${JSON.stringify(r.json).slice(0, 150)}`);
    });
  } else log('HUB_BIN não definido: pulei a implantação de versão nova (só Aplicar)');
} else {
  const dbRows = () => docker('exec', own(APPDB), 'psql', '-U', 'hub', '-d', 'servicos', '-tAc',
    "SELECT sum((xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', schemaname, tablename), false, true, '')))[1]::text::bigint) FROM pg_tables WHERE schemaname='public'");
  const before = dbRows();
  log(`linhas do banco do servicos antes: ${before}`);
  const recover = async (url, from) => { for (let i = 0; i < 240; i++) { try { const r = await fetch(`${HUB}${url}`, { headers: { cookie }, signal: AbortSignal.timeout(5000) }); if (r.status === 200) return (Date.now() - from) / 1000; } catch { /* still down */ } await wait(500); } return -1; };
  for (let i = 1; i <= 2; i++) {
    await phase(`docker kill app-hub-app-servicos #${i}`, async () => {
      const a = Date.now(); docker('kill', own(`${CP}app-servicos`));
      const s = await recover('/_frag/servicos/dashboard', a);
      if (s < 0) throw new Error('servicos não voltou em 120 s');
      log(`  servicos voltou a responder 200 em ${s.toFixed(1)} s (supervisor/restart do Docker)`);
    });
    await wait(10000);
  }
  await phase('docker restart app-hub-web', async () => {
    const a = Date.now(); docker('restart', own(`${CP}web`));
    for (let i = 0; i < 240; i++) { try { if ((await fetch(`${HUB}/healthz`, { signal: AbortSignal.timeout(2000) })).status === 200) break; } catch { /* starting */ } await wait(500); }
    await login(); // the session survives (state in the database); renews it for safety
    log(`  hub voltou em ${((Date.now() - a) / 1000).toFixed(1)} s`);
  });
  await wait(8000);
  await phase('docker restart app-hub-app-db (estado da plataforma e dos apps)', async () => {
    const a = Date.now(); docker('restart', own(APPDB));
    for (let i = 0; i < 240; i++) { try { if ((await fetch(`${HUB}/_frag/ativos/dashboard`, { headers: { cookie }, signal: AbortSignal.timeout(3000) })).status === 200) break; } catch { /* still */ } await wait(500); }
    log(`  hub respondendo de novo ${((Date.now() - a) / 1000).toFixed(1)} s depois do restart do banco de estado`);
  });
  await wait(8000);
  const after = dbRows();
  log(`linhas do banco do servicos depois: ${after} (${after === before ? 'iguais' : 'DIFERENTES'})`);
  rows.push({ name: 'integridade do banco do servicos', secs: '-', n: Number(before), ok: Number(after), bad: after === before ? {} : { divergiu: 1 }, err: '' });
}
stop = true;
await Promise.all(workers);
const total = window(t0, Date.now());
log(`total: ${total.n} req · 2xx ${total.ok} · não-2xx ${JSON.stringify(total.bad)}`);
const day = new Date().toISOString().slice(0, 10);
const outFile = path.resolve(arg('--out', path.join(here, 'results', day, `py-${MODE}.md`)));
fs.mkdirSync(path.dirname(outFile), { recursive: true });
const md = `# ${MODE === 'zd' ? 'Zero downtime' : 'Caos'} — pilha Python (${new Date().toISOString()})\n\nCarga ${RPS} req/s.\n\n| Fase | s | req | 2xx | não-2xx | erro |\n|---|---:|---:|---:|---|---|\n${rows.map((r) => `| ${r.name} | ${r.secs} | ${r.n} | ${r.ok} | ${JSON.stringify(r.bad)} | ${r.err} |`).join('\n')}\n\nTotal: ${total.n} req, 2xx ${total.ok}, não-2xx ${JSON.stringify(total.bad)}\n\n${lines.map((l) => `- ${l}`).join('\n')}\n`;
fs.writeFileSync(outFile, md);
console.log(`\nRelatório: ${outFile}`);
const failed = rows.some((r) => r.err) || (MODE === 'zd' && Object.keys(total.bad).length > 0);
process.exit(failed ? 1 : 0);
