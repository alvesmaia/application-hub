#!/usr/bin/env node
// Paleta da identidade Alvesmaia (claro + escuro grafite) a partir de um mapeamento EXPLÍCITO.
//
//   node scripts/gen-dark-theme.mjs          # regrava os blocos gerados
//   node scripts/gen-dark-theme.mjs --check  # falha se algo estiver desatualizado ou sem contraste (CI)
//
// Fonte única: `scripts/brand-palette.json`
//   - `roles`: papéis de cor (`page`, `surface`, `title`, `primary`, `chrome`…) com o valor
//     claro e o escuro escolhidos à mão (nada é calculado);
//   - `primitives`: a qual papel cada primitiva `--c-*` herdada do mockup aponta (ou
//     `{ "fixed": "#hex" }` para cores que não mudam com o tema — ex.: o terminal de logs,
//     que é escuro nos dois temas);
//   - `contrast`: pares texto/fundo (≥ 4,5:1) e forma/fundo (≥ 3:1) conferidos nos DOIS temas.
//
// Saídas:
// 1. `assets/shell/tokens.css`, bloco `paleta:inicio/fim`: `--am-<papel>` (claro) e
//    `--c-*: var(--am-<papel>)`; bloco `tema-escuro:inicio/fim`: só os `--am-<papel>` escuros
//    (as primitivas e semânticas seguem por `var()`), em `:root[data-theme="dark"]` e, com
//    a escolha "Automático (sistema)" (`data-theme="auto"`), em `prefers-color-scheme: dark`.
// 2. Ponte de temas dos apps (fim de tokens.css): papéis do hub como `var(--app-<papel>, …)`
//    dentro da área do app — cada app tem a SUA identidade (CONTRATO-APP §29); o script
//    não reescreve o CSS dos apps, só confere (`--check`) o contraste dos `--app-*` deles.
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

// 1. Tokens da plataforma.
{
  const file = path.join(root, 'assets/shell/tokens.css');
  const src = fs.readFileSync(file, 'utf8');
  const i = src.indexOf('/* paleta:inicio'), j = src.indexOf(PAL_END);
  if (i < 0 || j < 0) throw new Error('tokens.css sem os marcadores paleta:inicio/fim');
  const prims = Object.entries(primitives).map(([k, v]) => [k, typeof v === 'string' ? `var(--am-${v})` : v.fixed]);
  const pal = `${PAL_BEGIN}\n:root {\n  /* Papéis da identidade (tema claro). */\n${decl(roleDecls('light'))}\n  /* Primitivas herdadas do mockup → papéis. */\n${decl(prims)}\n}\n${PAL_END}`;
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
  // O que vem depois do bloco escuro (a ponte de temas dos apps, seção 2) é preservado.
  const e = out.indexOf(END);
  const tail = k >= 0 && e >= 0 ? out.slice(e + END.length).replace(/^\n+/, '') : '';
  write(file, `${base}\n${blk}${tail ? `\n${tail}` : ''}`);
}

// 2. Ponte de temas dos apps (CONTRATO-APP §29), no fim de tokens.css: dentro da área do
//    app (#app-root e camadas [data-hub-layer]) cada papel do hub vira `var(--app-<papel>,
//    <valor do hub>)` e os tokens derivados (`--c-*`, `--color-*`, fontes) são redeclarados
//    ali — o app que define `--app-primary` etc. no seu `:root` (confinado ao #app-root) muda
//    tudo o que o hub desenha na área dele; sem a variável, vale a identidade do hub.
//    O script NÃO reescreve o CSS dos apps: só confere o contraste dos `--app-*` deles (3).
{
  const file = path.join(root, 'assets/shell/tokens.css');
  const src = fs.readFileSync(file, 'utf8');
  const B2 = '/* temas-dos-apps:inicio (gerado por scripts/gen-dark-theme.mjs — não editar) */';
  const E2 = '/* temas-dos-apps:fim */';
  const k = src.indexOf(B2);
  const head = k >= 0 ? src.slice(0, k).replace(/\n+$/, '\n') : src.replace(/\n*$/, '\n');
  // Tokens derivados (fora dos blocos gerados) que dependem de outros por var().
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
  // Atalhos do CSS do shell (`--ink`, `--tsc-*` dos gráficos…) declarados em `:root{}` de
  // styles.css/hub.css a partir dos tokens: também recalculados na área do app.
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

// 3. Contraste das identidades dos apps de demonstração: `--app-<papel>` do `:root` (claro)
//    e do `:root[data-theme="dark"]` de cada CSS de app, nos mesmos pares conferidos no hub.
const APP_CSS = [
  'demo-apps/assets/ativos.css',
  'demo-apps/assets/app2.css',
  'demo-apps/assets/app3.css',
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
