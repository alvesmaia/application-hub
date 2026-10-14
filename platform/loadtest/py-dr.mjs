// F7a — DR against the Python stack (`app-hub-*`, hub on :8090). Touches nothing outside `app-hub-*`:
//
//   HUB=http://localhost:8090 HUB_USER=admin@demo.local HUB_PASSWORD=… node loadtest/py-dr.mjs [--out arq.md]
//
// 1. measures the reference: `servicos` (provisioned servicos database: rows + md5 of each table) and
//    `fornecedores` (3 new attachments in the bucket: sha256 of each one, downloaded by the app);
// 2. manual backup of both via the API (the "Fazer backup agora" button) and waits for it to finish;
// 3. DISASTER: DROP DATABASE servicos and all objects of the `fornecedores` bucket;
// 4. restores via the API (the "Restaurar" button), measures the RTO and checks everything byte by byte + apps responding.
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HUB = process.env.HUB || 'http://localhost:8090';
const USER = process.env.HUB_USER || 'admin@demo.local';
const PASS = process.env.HUB_PASSWORD || process.env.HUB_DEMO_PASSWORD || 'admin';
const DBC = process.env.PY_DB_CONTAINER || 'app-hub-app-db';
const OBJ = process.env.PY_OBJSTORE_CONTAINER || 'app-hub-objstore';
const OBJ_PASS = process.env.APP_HUB_OBJSTORE_PASSWORD || 'hubpy-objstore-dev';
const here = path.dirname(fileURLToPath(import.meta.url));
const t0 = Date.now();
const lines = [];
const log = (m) => { const l = `${((Date.now() - t0) / 1000).toFixed(1)} s — ${m}`; lines.push(l); console.log(l); };
const docker = (...a) => execFileSync('docker', a, { encoding: 'utf8' }).trim();
const psql = (db, sql) => docker('exec', DBC, 'psql', '-U', 'hub', '-d', db, '-tAc', sql);
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
// Guard: this script only touches app-hub-* containers.
for (const c of [DBC, OBJ]) if (!/^app-hub-/.test(c)) throw new Error(`container fora de app-hub-*: ${c}`);

let cookie = '';
async function login() {
  const r = await fetch(`${HUB}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: HUB },
    body: new URLSearchParams({ email: USER, password: PASS }),
  });
  cookie = (r.headers.get('set-cookie') || '').match(/hub_session=[^;]+/)[0];
}
async function http(method, url, body, headers = {}) {
  const r = await fetch(`${HUB}${url}`, { method, body, headers: { cookie, origin: HUB, ...headers } });
  const buf = Buffer.from(await r.arrayBuffer());
  return { status: r.status, buf, text: buf.toString('utf8') };
}
const api = async (method, url, json) => {
  const r = await http(method, `/api/ops${url}`, json ? JSON.stringify(json) : undefined, { 'content-type': 'application/json' });
  return { status: r.status, json: r.text ? JSON.parse(r.text) : null };
};

const SUP = 'FOR-0284';
async function attachmentsRef() {
  const page = await http('GET', `/_frag/fornecedores/indicators?id=${SUP}`);
  const names = [...page.text.matchAll(/attachments\/([^"/]+)"/g)].map((m) => m[1]);
  const out = {};
  for (const n of [...new Set(names)].sort()) {
    const r = await http('GET', `/_frag/fornecedores/suppliers/${SUP}/attachments/${n}`);
    out[decodeURIComponent(n)] = `${r.status}:${sha(r.buf)}:${r.buf.length}`;
  }
  return out;
}
function dbRef() {
  const tables = psql('servicos', "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1").split('\n').filter(Boolean);
  const out = {};
  for (const t of tables) {
    out[t] = psql('servicos', `SELECT count(*) || ':' || coalesce(md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))), '-') FROM "${t}" t`);
  }
  return out;
}

await login();
log('login ok');
for (let i = 0; i < 3; i++) {
  const fd = new FormData();
  const bytes = Buffer.concat([Buffer.from('%PDF-1.4\n'), crypto.randomBytes(20000 + i * 5000)]);
  fd.append('file', new Blob([bytes], { type: 'application/pdf' }), `dr-${Date.now()}-${i}.pdf`);
  const r = await http('POST', `/_frag/fornecedores/suppliers/${SUP}/attachments`, fd, { 'idempotency-key': crypto.randomUUID() });
  if (r.status >= 300) throw new Error(`upload ${r.status}: ${r.text.slice(0, 200)}`);
}
const ref = { db: dbRef(), att: await attachmentsRef() };
log(`referência: servicos ${Object.keys(ref.db).length} tabelas (${Object.values(ref.db).map((v) => v.split(':')[0]).join('/')} linhas) · ${Object.keys(ref.att).length} anexos`);
if (Object.keys(ref.att).length < 3) throw new Error('anexos não encontrados na ficha do fornecedor');

// ---- manual backup ---------------------------------------------------------------
const maxBefore = Math.max(0, ...(await api('GET', '/backups')).json.history.map((b) => b.id));
const done = {};
for (const app of ['servicos', 'fornecedores']) {
  const r = await api('POST', `/backups/${app}/run`, {});
  if (![200, 202].includes(r.status)) throw new Error(`backup ${app}: ${r.status} ${JSON.stringify(r.json)}`);
}
const tB = Date.now();
while (Object.keys(done).length < 2) {
  if (Date.now() - tB > 300000) throw new Error('backups não concluíram em 5 min');
  await wait(3000);
  for (const b of (await api('GET', '/backups')).json.history) {
    if (b.id > maxBefore && !done[b.app] && ['servicos', 'fornecedores'].includes(b.app) && /Sob demanda|Manual|Agendado/.test(b.kind)) {
      if (b.status === 'Concluído') { done[b.app] = b; log(`backup ${b.app}: ${b.detail} · ${(b.size / 1048576).toFixed(1)} MiB em ${(b.duration_ms / 1000).toFixed(1)} s`); }
      if (b.status === 'Falhou') throw new Error(`backup de ${b.app} falhou: ${b.detail}`);
    }
  }
}

// ---- disaster --------------------------------------------------------------------
const tDisaster = Date.now();
psql('postgres', 'DROP DATABASE servicos WITH (FORCE)');
docker('exec', OBJ, 'sh', '-c', `mc alias set s http://127.0.0.1:9000 hubadmin ${OBJ_PASS} >/dev/null && mc rm --recursive --force s/fornecedores`);
const attAfterDisaster = await attachmentsRef();
const broken = await http('GET', '/_frag/servicos/dashboard');
log(`DESASTRE: DROP DATABASE servicos (servicos/dashboard → HTTP ${broken.status}), bucket fornecedores vazio (${Object.keys(attAfterDisaster).length} anexos visíveis)`);

// ---- restore -----------------------------------------------------------------
const rto = {};
await Promise.all(['servicos', 'fornecedores'].map(async (app) => {
  const t = Date.now();
  const r = await api('POST', `/backups/records/${done[app].id}/restore`, {});
  if (![200, 202].includes(r.status)) throw new Error(`restore ${app}: ${r.status} ${JSON.stringify(r.json)}`);
  for (;;) {
    await wait(1000);
    const j = (await api('GET', `/jobs/${r.json.id}`)).json;
    if (j.done) {
      if (!j.ok) throw new Error(`restauração de ${app} falhou: ${j.message}`);
      rto[app] = (Date.now() - t) / 1000;
      log(`restaurado ${app} em ${rto[app].toFixed(1)} s: ${j.message}`);
      break;
    }
  }
}));
let tOk = 0;
for (let i = 0; i < 90; i++) {
  const c = await http('GET', '/_frag/servicos/dashboard');
  const f = await http('GET', `/_frag/fornecedores/indicators?id=${SUP}`);
  if (c.status === 200 && f.status === 200) { tOk = Date.now(); break; }
  await wait(1000);
}
const after = { db: dbRef(), att: await attachmentsRef() };
const rtAfter = (await api('GET', '/runtime')).json.apps.filter((x) => x.env === 'producao' && x.maintenance);
const checks = [
  ['tabelas do servicos: linhas + md5 de cada uma', JSON.stringify(ref.db) === JSON.stringify(after.db), `${Object.keys(after.db).length} tabelas`],
  ['anexos byte a byte (sha256 pelo app)', JSON.stringify(ref.att) === JSON.stringify(after.att), `${Object.keys(after.att).length} anexos`],
  ['apps respondendo depois (servicos/dashboard e ficha do fornecedor)', tOk > 0, tOk ? 'HTTP 200' : 'falhou'],
  ['manutenção desligada no fim', rtAfter.length === 0, rtAfter.length ? 'ainda ligada' : 'desligada'],
];
checks.forEach(([n, ok, d]) => log(`${ok ? 'OK ' : 'FALHOU'} ${n} — ${d}`));
const total = ((tOk || Date.now()) - tDisaster) / 1000;
const day = new Date().toISOString().slice(0, 10);
const outArg = process.argv.indexOf('--out');
const outFile = outArg > 0 ? path.resolve(process.argv[outArg + 1]) : path.join(here, 'results', day, 'py-dr.md');
fs.mkdirSync(path.dirname(outFile), { recursive: true });
fs.writeFileSync(outFile, `# DR — pilha Python (${new Date().toISOString()})\n\nRTO: ${Object.entries(rto).map(([a, s]) => `${a} ${s.toFixed(1)} s`).join(' · ')} · do desastre ao app respondendo: ${total.toFixed(1)} s\n\n${lines.map((l) => `- ${l}`).join('\n')}\n`);
console.log(`\nRelatório: ${outFile}`);
if (checks.some(([, ok]) => !ok)) process.exit(1);
