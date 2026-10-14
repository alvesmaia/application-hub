#!/usr/bin/env node
// Alvesmaia brand palette (light + graphite dark) generated from an EXPLICIT mapping.
//
//   node scripts/gen-dark-theme.mjs          # rewrites the generated blocks
//   node scripts/gen-dark-theme.mjs --check  # fails if anything is out of date or lacks contrast (CI)
//
// Single source: `scripts/brand-palette.json`
//   - `roles`: color roles (`page`, `surface`, `title`, `primary`, `chrome`…) with the value
//     for light and dark, hand-picked (nothing is computed);
//   - `primitives`: which role each `--c-*` primitive inherited from the mockup points to (or
//     `{ "fixed": "#hex" }` for colors that do not change with the theme, e.g. the log terminal,
//     which is dark in both themes);
//   - `contrast`: text/background pairs (≥ 4,5:1) and shape/background pairs (≥ 3:1) checked in BOTH themes.
//
// Outputs:
// 1. `assets/shell/tokens.css`, block `paleta:inicio/fim`: `--am-<papel>` (light) and
//    `--c-*: var(--am-<papel>)`; block `tema-escuro:inicio/fim`: only the dark `--am-<papel>`
//    (primitives and semantics follow through `var()`), under `:root[data-theme="dark"]` and, with
//    the "Automático (sistema)" choice (`data-theme="auto"`), under `prefers-color-scheme: dark`.
// 2. App theme bridge (end of tokens.css): hub roles as `var(--app-<papel>, …)`
//    inside the app area; each app has ITS OWN identity (CONTRATO-APP §29); the script
//    does not rewrite the apps' CSS, it only checks (`--check`) their `--app-*` contrast.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const check = process.argv.includes('--check');
const PAL_BEGIN = '/* paleta:inicio (gerado por scripts/gen-dark-theme.mjs a partir de scripts/brand-palette.json — não editar) */';
const PAL_END = '/* paleta:fim */';
const BEGIN = '/* tema-escuro:inicio (gerado por scripts/gen-dark-theme.mjs — não editar) */';
const END = '/* tema-escuro:fim */';

const palette = JSON.parse(fs.readFileSync(path.join(root, 'scripts/brand-palette.json'), 'utf8'));
const { roles, primitives } = palette;

// ---------------------------------------------------------------- contraste (WCAG 2.x)
function rgb(hex) {
  let x = hex.replace('#', '');
  if (x.length === 3) x = [...x].map((c) => c + c).join('');
  const n = parseInt(x.slice(0, 6), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function lum(hex) {
  return rgb(hex)
    .map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
}
export function contrast(a, b) {
  const x = lum(a), y = lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
const problems = [];
for (const theme of ['light', 'dark']) {
  for (const [list, min] of [[palette.contrast.text, 4.5], [palette.contrast.nonText, 3]]) {
    for (const [fg, bg] of list) {
      if (!roles[fg] || !roles[bg]) { problems.push(`par com papel desconhecido: ${fg}/${bg}`); continue; }
      const c = contrast(roles[fg][theme], roles[bg][theme]);
      if (c < min) problems.push(`${theme}: ${fg} (${roles[fg][theme]}) sobre ${bg} (${roles[bg][theme]}) = ${c.toFixed(2)}:1 < ${min}:1`);
    }
  }
}
for (const [k, v] of Object.entries(primitives)) {
  if (typeof v === 'string' && !roles[v]) problems.push(`primitiva ${k} aponta para papel inexistente: ${v}`);
}

// ---------------------------------------------------------------- escrita
let stale = [];
function write(file, next) {
  const cur = fs.readFileSync(file, 'utf8');
  if (cur === next) return;
  if (check) { stale.push(path.relative(root, file)); return; }
  fs.writeFileSync(file, next);
  console.log(`atualizado: ${path.relative(root, file)}`);
}
const decl = (list) => list.map(([k, v]) => `  ${k}: ${v};`).join('\n');
const roleDecls = (theme) => Object.entries(roles).map(([k, v]) => [`--am-${k}`, v[theme]]);

// 1. Platform tokens.
{
  const file = path.join(root, 'assets/shell/tokens.css');
  const src = fs.readFileSync(file, 'utf8');
  const i = src.indexOf('/* paleta:inicio'), j = src.indexOf(PAL_END);
  if (i < 0 || j < 0) throw new Error('tokens.css sem os marcadores paleta:inicio/fim');
  const prims = Object.entries(primitives).map(([k, v]) => [k, typeof v === 'string' ? `var(--am-${v})` : v.fixed]);
  const pal = `${PAL_BEGIN}\n:root {\n  /* Identity roles (light theme). */\n${decl(roleDecls('light'))}\n  /* Primitives inherited from the mockup → roles. */\n${decl(prims)}\n}\n${PAL_END}`;
  let out = src.slice(0, i) + pal + src.slice(j + PAL_END.length);
  const extra = [
    ['--shadow-card', 'none'],
    ['--shadow-popover', '0 18px 44px rgba(0, 0, 0, .44)'],
    ['--shadow-modal', '0 18px 44px rgba(0, 0, 0, .44)'],
  ];
  const body = decl([...roleDecls('dark'), ...extra]);
  const indent = (s) => s.replace(/^(?=.)/gm, '  ');
  const blk = `${BEGIN}\n:root[data-theme="dark"] {\n  color-scheme: dark;\n${body}\n}\n@media (prefers-color-scheme: dark) {\n  :root[data-theme="auto"] {\n    color-scheme: dark;\n${indent(body)}\n  }\n}\n${END}\n`;
  const k = out.indexOf(BEGIN);
  const base = k >= 0 ? out.slice(0, k).replace(/\n+$/, '\n') : out.replace(/\n*$/, '\n');
  // Whatever follows the dark block (the app theme bridge, section 2) is preserved.
  const e = out.indexOf(END);
  const tail = k >= 0 && e >= 0 ? out.slice(e + END.length).replace(/^\n+/, '') : '';
  write(file, `${base}\n${blk}${tail ? `\n${tail}` : ''}`);
}

// 2. App theme bridge (CONTRATO-APP §29), at the end of tokens.css: inside the app area
//    (#app-root and [data-hub-layer] layers) each hub role becomes `var(--app-<papel>,
//    <valor do hub>)` and the derived tokens (`--c-*`, `--color-*`, fonts) are redeclared
//    there — an app that defines `--app-primary` etc. in its `:root` (scoped to #app-root) changes
//    everything the hub draws in its area; without the variable, the hub identity applies.
//    The script does NOT rewrite the apps' CSS: it only checks the contrast of their `--app-*` (3).
{
  const file = path.join(root, 'assets/shell/tokens.css');
  const src = fs.readFileSync(file, 'utf8');
  const B2 = '/* temas-dos-apps:inicio (gerado por scripts/gen-dark-theme.mjs — não editar) */';
  const E2 = '/* temas-dos-apps:fim */';
  const k = src.indexOf(B2);
  const head = k >= 0 ? src.slice(0, k).replace(/\n+$/, '\n') : src.replace(/\n*$/, '\n');
  // Derived tokens (outside the generated blocks) that depend on others via var().
  const from = head.indexOf(PAL_END) + PAL_END.length;
  const to = head.indexOf(BEGIN) >= 0 ? head.indexOf(BEGIN) : head.length;
  const derived = [...head.slice(from, to).matchAll(/^\s*(--(?:color|focus|font)-[\w-]+)\s*:\s*([^;]*var\([^;]*);/gm)]
    .map((m) => [m[1], m[2].trim()]);
  const appFont = { '--font-body': '--app-font', '--font-ui': '--app-font', '--font-display': '--app-font-display' };
  const derivedOut = derived.map(([n, v]) => {
    if (!appFont[n]) return [n, v];
    const hub = v;
    return [n, n === '--font-display' ? `var(--app-font-display, var(--app-font, ${hub}))` : `var(--app-font, ${hub})`];
  });
  // Shell CSS shortcuts (`--ink`, `--tsc-*` for the charts…) declared in `:root{}` of
  // styles.css/hub.css from the tokens: also recalculated in the app area.
  const shellVars = new Map();
  for (const name of ['styles.css', 'hub.css']) {
    const css = fs.readFileSync(path.join(root, 'assets/shell', name), 'utf8');
    for (const m of css.matchAll(/(?:^|[}\s]):root\{([^}]*)\}/g)) {
      for (const d of m[1].matchAll(/(--[\w-]+)\s*:\s*([^;]*var\([^;]*?)\s*(?:;|$)/g)) shellVars.set(d[1], d[2].trim());
    }
  }
  for (const [n] of derivedOut) shellVars.delete(n);
  derivedOut.push(...shellVars);
  const prims = Object.entries(primitives).filter(([, v]) => typeof v === 'string').map(([n, v]) => [n, `var(--am-${v})`]);
  const bridge = (theme) => Object.entries(roles).map(([n, v]) => [`--am-${n}`, `var(--app-${n}, ${v[theme]})`]);
  const SEL = '#app-root, [data-hub-layer]';
  const dsel = (p) => `${p} #app-root, ${p} [data-hub-layer]`;
  const indent = (x) => x.replace(/^(?=.)/gm, '  ');
  const blk = [
    B2,
    `${SEL} {`, decl(bridge('light')), decl(prims), decl(derivedOut), '}',
    `${dsel(':root[data-theme="dark"]')} {`, decl(bridge('dark')), '}',
    '@media (prefers-color-scheme: dark) {', `  ${dsel(':root[data-theme="auto"]')} {`, indent(decl(bridge('dark'))), '  }', '}',
    E2, '',
  ].join('\n');
  write(file, `${head}\n${blk}`);
}

// 3. Contrast of the demo apps' identities: `--app-<papel>` of `:root` (light)
//    and of `:root[data-theme="dark"]` in each app CSS, for the same pairs checked in the hub.
const APP_CSS = [
  'demo-apps-py/ativos/src/ativos/assets/app.css',
  'demo-apps-py/fornecedores/src/fornecedores/assets/app.css',
  'demo-apps-py/servicos/src/servicos/assets/app.css',
  'demo-apps-py/coletor/src/coletor/assets/app.css',
];
for (const rel of APP_CSS) {
  const file = path.join(root, rel);
  if (!fs.existsSync(file)) { problems.push(`${rel}: arquivo não encontrado`); continue; }
  const css = fs.readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  const take = (re) => {
    const o = {};
    for (const m of css.matchAll(re)) for (const d of m[1].matchAll(/--app-([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) o[d[1]] = d[2];
    return o;
  };
  const light = take(/(?:^|\})\s*:root\s*\{([^}]*)\}/g);
  const dark = take(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/g);
  if (!Object.keys(light).length) { problems.push(`${rel}: sem tokens --app-* no :root`); continue; }
  if (!Object.keys(dark).length) { problems.push(`${rel}: sem variante escura (:root[data-theme="dark"])`); continue; }
  for (const [theme, vals] of [['light', light], ['dark', { ...light, ...dark }]]) {
    const val = (r) => vals[r] || roles[r][theme];
    for (const [list, min] of [[palette.contrast.text, 4.5], [palette.contrast.nonText, 3]]) {
      for (const [fg, bg] of list) {
        if (!roles[fg] || !roles[bg]) continue;
        const cr = contrast(val(fg), val(bg));
        if (cr < min) problems.push(`${rel} ${theme}: ${fg} (${val(fg)}) sobre ${bg} (${val(bg)}) = ${cr.toFixed(2)}:1 < ${min}:1`);
      }
    }
  }
}

if (problems.length) {
  console.error(`paleta: ${problems.length} problema(s)\n  ${problems.join('\n  ')}`);
  process.exit(1);
}
if (check && stale.length) {
  console.error(`tema desatualizado: ${stale.join(', ')} — rode node scripts/gen-dark-theme.mjs`);
  process.exit(1);
}
