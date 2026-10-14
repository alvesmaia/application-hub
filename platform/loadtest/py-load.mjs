// Load: how many simultaneous users the hub handles. Virtual users (closed loop, with think time) run the
// script of a real person — Início, catalog, open the app, navigate, search — in load steps.
//
//   HUB=http://127.0.0.1:8097 HUB_DEMO_PASSWORD=… node loadtest/py-load.mjs [--steps 25,50,100,200] [--dur 60]
//        [--think 2000] [--app ativos] [--out file.md]
//
// Run it on an isolated stack (install.sh --project app-hub-<x>), never on the owner's instance. Criterion per step:
// p95 < 300 ms and errors < 0,1%. "Supports" = highest step that passed. Sessions: the 3 demo users
// (HUB_DEMO_USERS=1), split among the virtual users (login is rate-limited).
import fs from 'node:fs';

const HUB = process.env.HUB || 'http://127.0.0.1:8097';
const PASS = process.env.HUB_DEMO_PASSWORD;
const arg = (n, d) => { const i = process.argv.indexOf(n); return i > 0 ? process.argv[i + 1] : d; };
const STEPS = arg('--steps', '25,50,100,200').split(',').map(Number);
const DUR = Number(arg('--dur', 60)) * 1000;
const THINK = Number(arg('--think', 2000));
const APP = arg('--app', 'ativos');
const OUT = arg('--out', '');
const P95_MAX = 300, ERR_MAX = 0.001;
if (!PASS) { console.error('defina HUB_DEMO_PASSWORD (usuários de demonstração da pilha isolada)'); process.exit(2); }

const ROUTE = [
  ['/', {}],
  ['/applications', {}],
  [`/apps/${APP}`, {}],
  [`/_frag/${APP}/dashboard`, { 'x-hub-nav': '1' }],
  [`/_frag/${APP}/assets`, { 'x-hub-nav': '1' }],
  [`/_frag/${APP}/assets?q=NT-1`, {}],
  ['/inbox', {}],
];

async function login(email) {
  const r = await fetch(`${HUB}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: HUB },
    body: new URLSearchParams({ email, password: PASS, next: '/' }),
  });
  const c = (r.headers.get('set-cookie') || '').match(/hub_session=[^;]+/);
  if (!c) throw new Error(`login ${email}: HTTP ${r.status}`);
  return c[0];
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const pct = (xs, p) => xs.length ? xs[Math.min(xs.length - 1, Math.floor((p / 100) * xs.length))] : 0;

async function step(n, cookies) {
  const lat = [], bad = new Map();
  let stop = false, total = 0;
  const vu = async (i) => {
    await wait(Math.random() * THINK); // spreads the start
    for (let k = i; !stop; k++) {
      const [url, h] = ROUTE[k % ROUTE.length];
      const t = performance.now();
      let st = 0;
      try {
        const r = await fetch(HUB + url, { headers: { cookie: cookies[i % cookies.length], ...h }, redirect: 'manual' });
        await r.arrayBuffer();
        st = r.status;
      } catch { st = -1; }
      if (stop) break;
      total++;
      lat.push(performance.now() - t);
      if (st < 200 || st >= 400) bad.set(st, (bad.get(st) || 0) + 1);
      await wait(THINK * (0.5 + Math.random()));
    }
  };
  const t0 = Date.now();
  const run = Promise.all(Array.from({ length: n }, (_, i) => vu(i)));
  await wait(DUR);
  stop = true;
  await run;
  lat.sort((a, b) => a - b);
  const errs = [...bad.values()].reduce((a, b) => a + b, 0);
  const r = {
    users: n, reqs: total, rps: total / ((Date.now() - t0) / 1000),
    p50: pct(lat, 50), p95: pct(lat, 95), p99: pct(lat, 99), err: total ? errs / total : 1,
    bad: [...bad].map(([s, c]) => `${s}×${c}`).join(' '),
  };
  r.ok = r.p95 < P95_MAX && r.err < ERR_MAX;
  return r;
}

const cookies = [];
for (const u of ['admin', 'operador', 'leitor']) cookies.push(await login(`${u}@demo.local`));
const rows = [];
for (const n of STEPS) {
  const r = await step(n, cookies);
  rows.push(r);
  console.log(`${n} usuários: ${r.rps.toFixed(1)} req/s · p50 ${r.p50.toFixed(0)} ms · p95 ${r.p95.toFixed(0)} ms · `
    + `p99 ${r.p99.toFixed(0)} ms · erros ${(r.err * 100).toFixed(2)}% ${r.bad} → ${r.ok ? 'OK' : 'FALHOU'}`);
  if (!r.ok) break; // the next step would only get worse
}
const best = rows.filter((r) => r.ok).at(-1);
const md = [
  `# Carga — ${new Date().toISOString()}`, '',
  `Hub ${HUB} · app ${APP} · ${DUR / 1000} s por degrau · pensar ~${THINK} ms · critério p95 < ${P95_MAX} ms e erros < ${ERR_MAX * 100}%`, '',
  '| Usuários | req/s | p50 ms | p95 ms | p99 ms | Erros | Resultado |', '|---|---|---|---|---|---|---|',
  ...rows.map((r) => `| ${r.users} | ${r.rps.toFixed(1)} | ${r.p50.toFixed(0)} | ${r.p95.toFixed(0)} | ${r.p99.toFixed(0)} | `
    + `${(r.err * 100).toFixed(2)}% ${r.bad} | ${r.ok ? 'OK' : 'falhou'} |`),
  '', `**Suporta: ${best ? best.users : 0} usuários simultâneos.**`, '',
].join('\n');
console.log(md);
if (OUT) fs.writeFileSync(OUT, md);
process.exit(best ? 0 : 1);
