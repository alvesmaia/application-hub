// Compatibility of an app's CSS framework with the hub (CONTRATO-APP §29): the same native controls
// (compat/fixture.html) styled by plain HTML, Tailwind (preflight on and off) and Bootstrap 5 are measured
// in a clean page (reference) and inside a hub app area (#app-root, the CSS confined by HubSafe.scopedSheet,
// exactly as the hub loads an app's CSS). Differences in computed style = what the hub changes; the report
// lists them with contrast checks. Needs a running hub (HUB) and the E2E admin session (.auth/admin.json);
// run by tests/39-compat-frameworks.spec.ts, or alone: `node compat/run.mjs` → .work/compat-report.md (exit 1 =
// a problem the hub causes).
import { chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const HUB = process.env.HUB || 'http://127.0.0.1:8090';
const fixture = fs.readFileSync(path.join(HERE, 'fixture.html'), 'utf8');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');
function tailwind(variant) { // Tailwind builds only the classes the fixture uses (with and without its preflight reset)
  const out = path.join(ROOT, `.work/tw-${variant}.css`);
  const cli = path.join(ROOT, 'node_modules/tailwindcss/lib/cli.js');
  execFileSync(process.execPath, [cli, '-c', path.join(HERE, `tw.${variant}.config.cjs`), '-i', path.join(HERE, 'tw.in.css'), '-o', out, '--minify'], { stdio: 'ignore' });
  return fs.readFileSync(out, 'utf8');
}
fs.mkdirSync(path.join(ROOT, '.work'), { recursive: true });
const VARIANTS = {
  'html-puro': '',
  'tailwind-preflight': tailwind('preflight'),
  'tailwind-sem-preflight': tailwind('nopreflight'),
  'bootstrap-5': read('node_modules/bootstrap/dist/css/bootstrap.min.css'),
};
const PROPS = ['appearance', 'border-top-style', 'border-top-width', 'border-top-color', 'background-color', 'background-image',
  'color', 'padding-right', 'height', 'border-top-left-radius', 'font-size', 'accent-color'];

// In the page: computed style of every [data-c] control + what contrast/arrow checks need.
function measure() {
  const lum = (c) => {
    const m = String(c).match(/[\d.]+/g); if (!m) return null;
    const [r, g, b, a = 1] = m.map(Number); if (a === 0) return null;
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); if (x == null || y == null) return null; return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; if (lum(c) != null) return c; } return 'rgb(255, 255, 255)'; };
  const out = {};
  for (const el of document.querySelectorAll('[data-c]')) {
    const cs = getComputedStyle(el); const props = {};
    for (const p of window.__PROPS) props[p] = cs.getPropertyValue(p);
    const around = bgOf(el.parentElement); const own = lum(cs.backgroundColor) != null ? cs.backgroundColor : around;
    const arrowIcon = el.nextElementSibling?.matches('[data-c-arrow]') ? el.nextElementSibling : null; // the app's own arrow
    out[el.dataset.c] = {
      props,
      inheritsColor: cs.color === getComputedStyle(el.parentElement).color, // `color: inherit` (Tailwind preflight)
      borderContrast: cs.borderTopStyle === 'none' || parseFloat(cs.borderTopWidth) === 0 ? 0 : ratio(cs.borderTopColor, around),
      fillContrast: ratio(own, around),
      textContrast: ratio(cs.color, own),
      arrows: (cs.backgroundImage.match(/url\(/g) || []).length + (arrowIcon && getComputedStyle(arrowIcon).display !== 'none' ? 1 : 0),
    };
  }
  out['@root'] = { rem: getComputedStyle(document.documentElement).fontSize };
  return out;
}

async function reference(browser, css) {
  const p = await browser.newPage();
  await p.setContent(`<!doctype html><html><head><style>${css}</style></head><body style="background:#fff">${fixture}</body></html>`);
  await p.evaluate((props) => { window.__PROPS = props; }, PROPS);
  const r = await p.evaluate(measure);
  await p.close();
  return r;
}

async function insideHub(ctx, css, theme) {
  const p = await ctx.newPage();
  await p.goto(`${HUB}/apps/collector`);
  await p.waitForSelector('#app-root', { state: 'attached', timeout: 30000 });
  await p.waitForTimeout(800);
  const r = await p.evaluate(async ({ css, html, theme, props }) => {
    document.documentElement.dataset.theme = theme;
    document.adoptedStyleSheets = []; // drop the sample app's own CSS: only the framework under test
    if (css) document.adoptedStyleSheets = [window.HubSafe.scopedSheet(css)];
    document.getElementById('app-root').innerHTML = html;
    window.__PROPS = props;
    await new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)));
    return null;
  }, { css, html: fixture, theme, props: PROPS });
  const m = await p.evaluate(measure);
  await p.close();
  return m;
}

// Problems = what the hub makes worse than the same CSS in a clean page; the rest is informative
// (framework defaults, an app without a dark theme, the hub's look on unstyled controls).
// (font-size is left out: text inherits the hub's 13px body by design; rem sizes are checked on the root)
const LOOK = ['border-top-color', 'border-top-width', 'border-top-left-radius', 'background-color', 'color'];
function findings(variant, theme, ref, hub, plain) {
  const out = [];
  const add = (c, kind, detail, problem) => out.push({ variant, theme, c, kind, detail, problem });
  if (hub['@root'].rem !== ref['@root'].rem) add('@root', 'rem-alterado', `${ref['@root'].rem} → ${hub['@root'].rem}`, true);
  for (const [c, h] of Object.entries(hub)) {
    const r = ref[c]; if (!r || c === '@root') continue;
    const field = /^(input|textarea|select)/.test(c) && !/checkbox|radio|range|file/.test(c);
    const worse = (k, min) => h[k] !== null && h[k] < min && (r[k] === null || r[k] >= min); // fine outside, fails inside
    if (field && worse('borderContrast', 3) && (h.fillContrast ?? 1) < 3) add(c, 'borda-invisivel', `borda ${h.borderContrast.toFixed(2)}:1 (fora do hub ${r.borderContrast?.toFixed(2)}:1)`, theme === 'light');
    if ((field || c.startsWith('button')) && worse('textContrast', 4.5)) add(c, 'texto-baixo-contraste', `${h.textContrast.toFixed(2)}:1 (${h.props.color})`, theme === 'light');
    if (c.startsWith('select') && h.arrows > 1) add(c, 'seta-dupla', `${h.arrows} setas`, c !== 'select-own-arrow');
    const changed = PROPS.filter((p) => r.props[p] !== h.props[p]);
    // the app's own look = what its framework set (differs from the same markup without CSS) and the hub changed
    const overridden = theme === 'light' ? changed.filter((p) => LOOK.includes(p) && r.props[p] !== plain[c]?.props[p] && !(p === 'color' && r.inheritsColor)) : [];
    if (overridden.length) add(c, 'estilo-do-app-sobrescrito', overridden.map((p) => `${p}: ${r.props[p]} → ${h.props[p]}`).join(' · '), true);
    else if (changed.length) add(c, 'alterado-pelo-hub', changed.map((p) => `${p}: ${r.props[p]} → ${h.props[p]}`).join(' · '), false);
  }
  return out;
}

const browser = await chromium.launch();
// signed in as admin by the E2E setup (`npx playwright test --project=setup` writes .auth/admin.json)
const ctx = await browser.newContext({ bypassCSP: true, viewport: { width: 1440, height: 900 }, storageState: path.join(ROOT, '.auth/admin.json') });
const all = [];
const plain = await reference(browser, '');
for (const [variant, css] of Object.entries(VARIANTS)) {
  const ref = await reference(browser, css);
  for (const theme of ['light', 'dark']) all.push(...findings(variant, theme, ref, await insideHub(ctx, css, theme), plain));
}
await browser.close();
const problems = all.filter((f) => f.problem);
const lines = ['# Compatibilidade de frameworks com o hub', '', `Problemas causados pelo hub: **${problems.length}** · informativos: ${all.length - problems.length}`, '',
  '| Variante | Tema | Controle | Tipo | Problema | Detalhe |', '|---|---|---|---|---|---|',
  ...[...problems, ...all.filter((f) => !f.problem)].map((f) => `| ${f.variant} | ${f.theme} | ${f.c} | ${f.kind} | ${f.problem ? 'sim' : '—'} | ${f.detail.replace(/\|/g, '/')} |`)];
fs.mkdirSync(path.join(ROOT, '.work'), { recursive: true });
fs.writeFileSync(path.join(ROOT, '.work/compat-report.md'), lines.join('\n') + '\n');
console.log(`[compat] ${problems.length} problema(s) causado(s) pelo hub; ${all.length - problems.length} informativo(s) → .work/compat-report.md`);
process.exit(problems.length ? 1 : 0);
