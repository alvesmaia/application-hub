// Paridade Rust x Python. Uso: node platform-py/parity/compare.mjs / /login ... [--routes arquivo.json]
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire('C:/PROJETOS/PROD/application-hub/platform/tests-e2e/package.json');
const { chromium } = require('playwright');
const rules = JSON.parse(fs.readFileSync(path.join(here, 'ignore.json'), 'utf8'));
const HUBS = { rust: process.env.RUST_URL || 'http://localhost:8080', py: process.env.PY_URL || 'http://localhost:8090' };
const PIX_MAX = 0.5;
const SUBST = JSON.parse(process.env.PY_SUBST || '{}'); // trocas na rota do Python (ex.: id de cliente OAuth proprio de cada hub)
const routeFor = (k, r) => (k === 'py' ? Object.entries(SUBST).reduce((x, [a, b]) => x.split(a).join(b), r) : r);
const envFile = fs.readFileSync(path.join(here, '../.dev.env'), 'utf8').split(/\r?\n/).filter((l) => l.includes('='));
const env = Object.fromEntries(envFile.map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const PASS = process.env.HUB_DEMO_PASSWORD || env.HUB_DEMO_PASSWORD;

const args = process.argv.slice(2);
const ci = args.indexOf('--click');
const CLICK = ci >= 0 ? args.splice(ci, 2)[1] : ''; // estado a comparar: clica no seletor (ex.: modal aberto) antes de capturar
const ri = args.indexOf('--routes');
let routes = ri >= 0 ? JSON.parse(fs.readFileSync(args.splice(ri, 2)[1], 'utf8')) : args;
routes = routes.map((r) => r.replace(/^[A-Za-z]:[\/]Program Files[\/]Git/, '') || '/'); // Git Bash converte '/' em caminho do Windows
if (!routes.length) routes = JSON.parse(fs.readFileSync(path.join(here, 'routes.json'), 'utf8'));

// Roda no navegador. root = elemento raiz. Gera linhas "caminho [attrs ordenados] "texto"".
const normalize = ({ root, rules }) => {
  const rx = (a) => a.map((s) => new RegExp(s, 'g'));
  const textRe = rx(rules.textPatterns), valRe = rx(rules.valuePatterns), drop = new Set(rules.dropAttrs);
  const clean = (v, pats) => pats.reduce((s, p) => s.replace(p, '~'), v).replace(/\s+/g, ' ').trim();
  const skip = (el) => rules.ignoreSelectors.some((s) => el.matches(s))
    || (rules.ignoreText || []).some((r) => el.matches(r.selector) && el.textContent.includes(r.contains));
  const out = [];
  const walk = (el, p, i) => {
    const tag = el.tagName.toLowerCase(), me = `${p}/${tag}[${i}]`;
    if (['script', 'style', 'noscript'].includes(tag)) { out.push(me); return; }
    const attrs = [...el.attributes].filter((a) => !drop.has(a.name)).map((a) =>
      `${a.name}=${a.name === 'class' ? a.value.split(/s+/).filter(Boolean).sort().join(' ') : clean(a.value, valRe)}`).sort();
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ');
    out.push(`${me} [${attrs.join(' ')}] "${clean(own, textRe)}"`);
    const idx = {};
    for (const c of el.children) { if (skip(c)) continue; const t = c.tagName.toLowerCase(); idx[t] = (idx[t] || 0) + 1; walk(c, me, idx[t]); }
  };
  walk(root, '', 1);
  return out;
};

const rawLines = ({ html, rules, fn }) => {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return (0, eval)(`(${fn})`)({ root: doc.documentElement, rules });
};

async function login(browser, base) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light', locale: process.env.PARITY_LANG || 'pt-BR' });
  const pg = await ctx.newPage();
  await pg.goto(base + '/login', { waitUntil: 'load' });
  await pg.fill('#email', 'admin');
  await pg.fill('#password', PASS);
  await Promise.all([pg.waitForLoadState('load'), pg.press('#password', 'Enter')]);
  await pg.waitForTimeout(500);
  return { ctx, pg };
}

const diffLines = (a, b) => {
  const only = (x, y) => {
    const c = new Map();
    for (const l of y) c.set(l, (c.get(l) || 0) + 1);
    return x.filter((l) => { const n = c.get(l) || 0; if (n) { c.set(l, n - 1); return false; } return true; });
  };
  return { missing: only(a, b), extra: only(b, a) };
};
const slug = (r) => (r === '/' ? 'root' : r.replace(/^\/|\/$/g, '').replace(/[^\w-]+/g, '_')) + (CLICK ? '__' + CLICK.replace(/[^\w-]+/g, '_') : '');

const pixelDiff = ({ a, b }) => new Promise((resolve) => {
  const load = (s) => new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.src = 'data:image/png;base64,' + s; });
  Promise.all([load(a), load(b)]).then(([A, B]) => {
    const W = Math.max(A.width, B.width), H = Math.max(A.height, B.height);
    const cv = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
    const px = (i) => { const c = cv(W, H), x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, W, H); x.drawImage(i, 0, 0); return x.getImageData(0, 0, W, H).data; };
    const da = px(A), db = px(B), D = cv(W, H), dx = D.getContext('2d'), out = dx.createImageData(W, H);
    let n = 0;
    for (let i = 0; i < da.length; i += 4) {
      const bad = Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2]) > 48;
      if (bad) n++;
      out.data[i] = bad ? 255 : 230; out.data[i + 1] = bad ? 0 : 230; out.data[i + 2] = bad ? 0 : 230; out.data[i + 3] = 255;
    }
    dx.putImageData(out, 0, 0);
    const S = cv(W * 3 + 20, H), sx = S.getContext('2d');
    sx.fillStyle = '#888'; sx.fillRect(0, 0, S.width, H);
    sx.drawImage(A, 0, 0); sx.drawImage(B, W + 10, 0); sx.drawImage(D, 2 * W + 20, 0);
    resolve({ pct: (100 * n) / (W * H), side: S.toDataURL('image/png').split(',')[1] });
  });
});

const browser = await chromium.launch();
const S = {};
for (const k of Object.keys(HUBS)) S[k] = await login(browser, HUBS[k]);
const tool = await (await browser.newContext()).newPage(); // página neutra p/ DOMParser e canvas
const results = [];
const ANON = (r) => r === '/login' || r.startsWith('/login?'); // rotas sem sessão
const anon = {};
for (const k of Object.keys(HUBS)) { const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light', locale: process.env.PARITY_LANG || 'pt-BR' }); anon[k] = { ctx, pg: await ctx.newPage() }; }

for (const route of routes) {
  const dir = path.resolve(here, '../dist/parity', slug(route));
  fs.mkdirSync(dir, { recursive: true });
  const R = { route, hyd: {}, raw: {}, shots: {} };
  for (const k of Object.keys(HUBS)) {
    const { ctx, pg } = (ANON(route) ? anon : S)[k];
    try {
      const r = await ctx.request.get(HUBS[k] + routeFor(k, route), { failOnStatusCode: false });
      R.raw[k] = { status: r.status(), html: await r.text() };
    } catch { R.raw[k] = { status: 0, html: '' }; }
    R.raw[k].lines = await tool.evaluate(rawLines, { html: R.raw[k].html, rules, fn: normalize.toString() });
    try {
      await pg.setViewportSize({ width: 1440, height: 900 });
      await pg.goto(HUBS[k] + routeFor(k, route), { waitUntil: 'networkidle', timeout: 20000 }).catch(() => {});
      await pg.waitForTimeout(400);
      for (const sel of CLICK ? CLICK.split(";;") : []) {
        await pg.click(sel.trim());
        await pg.waitForTimeout(400);
      }
      R.hyd[k] =await pg.evaluate(`(${normalize.toString()})({root: document.documentElement, rules: ${JSON.stringify(rules)}})`);
      // dado variavel do backend: some dos dois lados tambem nos pixels (layout igual)
      await pg.evaluate(({ rules }) => {
        const st = document.createElement('style');
        st.textContent = rules.ignoreSelectors.join(',') + '{display:none!important}';
        document.head.append(st);
        for (const r of rules.ignoreText || []) document.querySelectorAll(r.selector).forEach((el) => { if (el.textContent.includes(r.contains)) el.style.display = 'none'; });
      }, { rules });
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        await pg.setViewportSize({ width: w, height: h });
        await pg.waitForTimeout(250);
        R.shots[`${k}${w}`] = path.join(dir, `${k}-${w}.png`);
        await pg.screenshot({ path: R.shots[`${k}${w}`], animations: 'disabled', caret: 'hide' });
      }
    } catch (e) { R.hyd[k] = [`ERRO: ${e.message}`]; }
  }
  R.hDiff = diffLines(R.hyd.rust, R.hyd.py);
  R.rDiff = diffLines(R.raw.rust.lines, R.raw.py.lines);
  R.pix = {};
  for (const w of [1440, 390]) {
    if (!R.shots[`rust${w}`] || !R.shots[`py${w}`] || !fs.existsSync(R.shots[`py${w}`])) { R.pix[w] = 100; continue; }
    const b64 = (f) => fs.readFileSync(f).toString('base64');
    const r = await tool.evaluate(pixelDiff, { a: b64(R.shots[`rust${w}`]), b: b64(R.shots[`py${w}`]) });
    fs.writeFileSync(path.join(dir, `side-by-side-${w}.png`), Buffer.from(r.side, 'base64'));
    R.pix[w] = r.pct;
  }
  fs.writeFileSync(path.join(dir, 'diff.json'), JSON.stringify({ route, status: { rust: R.raw.rust.status, py: R.raw.py.status }, hydrated: R.hDiff, raw: R.rDiff, pixelPct: R.pix }, null, 1));
  R.ok = !R.hDiff.missing.length && !R.hDiff.extra.length && !R.rDiff.missing.length && !R.rDiff.extra.length
    && R.pix[1440] <= PIX_MAX && R.pix[390] <= PIX_MAX && R.raw.rust.status === R.raw.py.status;
  results.push(R);
}
await browser.close();

console.log('rota | status rust/py | DOM-js falta/extra | DOM-raw falta/extra | pixels 1440/390 % | resultado');
for (const R of results) {
  console.log(`${R.route} | ${R.raw.rust.status}/${R.raw.py.status} | ${R.hDiff.missing.length}/${R.hDiff.extra.length} | ${R.rDiff.missing.length}/${R.rDiff.extra.length} | ${R.pix[1440].toFixed(2)}/${R.pix[390].toFixed(2)} | ${R.ok ? 'PASS' : 'FAIL'}`);
  for (const [t, l] of [['falta no py', R.hDiff.missing], ['extra no py', R.hDiff.extra]]) l.slice(0, 5).forEach((x) => console.log(`   ${t}: ${x.slice(0, 160)}`));
}
console.log('artefatos: platform-py/dist/parity/<rota>/');
process.exit(results.every((r) => r.ok) ? 0 : 1);
