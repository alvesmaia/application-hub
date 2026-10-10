// F8 — ao vivo: pacote com contexto de build (`hub pack --build-dir`) -> upload -> build no hub -> blue/green em
// produção com carga contínua (0 falhas) -> rollback. Só toca app-hub-*.
//   HUB=http://localhost:8090 HUB_PASSWORD=… node loadtest/py-build-live.mjs <pacote.hubapp> [--rps 40]
import fs from 'node:fs';

const HUB = process.env.HUB || 'http://localhost:8090';
const arg = (n, d) => { const i = process.argv.indexOf(n); return i > 0 ? process.argv[i + 1] : d; };
const pkg = process.argv[2];
const RPS = Number(arg('--rps', 40));
let cookie = '';
const t0 = Date.now();
const log = (m) => console.log(`${((Date.now() - t0) / 1000).toFixed(1)} s — ${m}`);

async function login() {
  const r = await fetch(`${HUB}/login`, { method: 'POST', redirect: 'manual',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: HUB },
    body: new URLSearchParams({ email: process.env.HUB_USER || 'admin@demo.local', password: process.env.HUB_PASSWORD || process.env.HUB_DEMO_PASSWORD || 'admin' }) });
  cookie = (r.headers.get('set-cookie') || '').match(/hub_session=[^;]+/)[0];
}
const H = { cookie, origin: HUB };
async function api(method, url, body) {
  const r = await fetch(`${HUB}/api/ops${url}`, { method, headers: { cookie, origin: HUB, 'x-hub-request': '1', ...(body instanceof FormData ? {} : { 'content-type': 'application/json' }) }, body });
  const t = await r.text();
  return { status: r.status, json: t ? JSON.parse(t) : null };
}

let stop = false; let n = 0; const bad = [];
async function worker() {
  while (!stop) {
    let st = 0;
    try { st = (await fetch(`${HUB}/_frag/ativos/dashboard`, { headers: { cookie }, signal: AbortSignal.timeout(15000) })).status; } catch { st = 0; }
    n++; if (st !== 200) bad.push(st);
    await new Promise((r) => setTimeout(r, 1000 / (RPS / 8)));
  }
}

await login();
const workers = Array.from({ length: 8 }, worker);
let ok = true;
try {
  const fd = new FormData();
  fd.append('package', new Blob([fs.readFileSync(pkg)]), 'p.hubapp');
  const up = await api('POST', '/packages?env=producao&wait=true', fd);
  const dep = up.json.result.deployment;
  log(`upload: ${up.status} ${dep.app} ${dep.version} ${dep.status} build=${dep.build} imagem=${dep.image}`);
  const ap = await api('POST', `/deployments/${dep.id}/approve?wait=true`);
  log(`aprovar (build + blue/green): ${ap.status} ${ap.json.status} · ${(ap.json.build_log || []).length} linhas de log`);
  ok &&= ap.json.status === 'Implantado';
  const rb = await api('POST', `/deployments/${dep.id}/rollback?wait=true`);
  log(`rollback: ${rb.status} ${rb.json.status} -> ${rb.json.version} (${rb.json.image})`);
  ok &&= rb.json.status === 'Implantado';
} catch (e) { log(`ERRO ${e}`); ok = false; }
stop = true; await Promise.all(workers);
log(`carga: ${n} pedidos, ${bad.length} fora de 200 ${bad.length ? JSON.stringify(bad) : ''}`);
process.exit(ok && bad.length === 0 ? 0 : 1);
