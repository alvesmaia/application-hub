#!/usr/bin/env node
// Varredura automática de UI do hub (@sweep).
//
//   node ui-sweep/run.mjs            matriz completa: admin/operador/leitor × claro/escuro/SO-escuro @1440 + 2 combinações @1280
//   node ui-sweep/run.mjs --quick    subconjunto (admin × claro/escuro @1440)
//   node ui-sweep/run.mjs --report <dir>   só refaz o relatório de uma pasta existente
//
// HUB (padrão http://localhost:8080) com os usuários de demonstração. Relatório em
// platform/loadtest/results/<data>/ui-sweep-<hora>/{report.md,findings.json,routes.json,shots/}.
// Sai ≠ 0 se houver achado fora de ui-sweep/allowlist.json (cada entrada com `reason`).
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const E2E = path.dirname(HERE);
const PLATFORM = path.dirname(E2E);
const args = process.argv.slice(2);
const quick = args.includes('--quick');
const reportOnly = args.includes('--report') ? args[args.indexOf('--report') + 1] : null;

const now = new Date();
const pad = (n) => String(n).padStart(2, '0');
const day = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
const hour = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
const out = reportOnly ? path.resolve(reportOnly) : (process.env.SWEEP_OUT || path.join(PLATFORM, 'loadtest', 'results', day, `ui-sweep-${hour}${quick ? '-quick' : ''}`));
fs.mkdirSync(out, { recursive: true });

const t0 = Date.now();
let pwStatus = 0;
if (!reportOnly) {
  fs.rmSync(path.join(out, 'findings'), { recursive: true, force: true });
  const r = spawnSync('npx', ['playwright', 'test', '-c', 'ui-sweep/playwright.sweep.config.ts'], {
    cwd: E2E, stdio: 'inherit', shell: process.platform === 'win32',
    env: { ...process.env, SWEEP_OUT: out, SWEEP_MODE: quick ? 'quick' : 'full' },
  });
  pwStatus = r.status ?? 1;
}
const secs = Math.round((Date.now() - t0) / 1000);

// ---- junta ----
const allow = JSON.parse(fs.readFileSync(path.join(HERE, 'allowlist.json'), 'utf8')).entries || [];
for (const a of allow) if (!a.reason || !a.check) { console.error('allowlist: toda entrada precisa de "check" e "reason":', a); process.exit(2); }
const re = (s) => (s ? new RegExp(s) : null);
const allowed = (f) => allow.find((a) => a.check === f.check
  && ['pattern', 'route', 'selector', 'message', 'role', 'theme', 'layer'].every((k) => !a[k] || re(a[k]).test(f[k] ?? '')));

const routes = fs.existsSync(path.join(out, 'routes.json')) ? JSON.parse(fs.readFileSync(path.join(out, 'routes.json'), 'utf8')) : { routes: [], excluded: [] };
const units = [];
const findings = [];
const fdir = path.join(out, 'findings');
for (const f of fs.existsSync(fdir) ? fs.readdirSync(fdir).sort() : []) {
  const j = JSON.parse(fs.readFileSync(path.join(fdir, f), 'utf8'));
  units.push(j);
  for (const x of j.findings) { const a = allowed(x); findings.push({ ...x, allowlisted: a ? a.reason : undefined }); }
}
const open = findings.filter((f) => !f.allowlisted);
const byCheck = {};
for (const f of findings) { byCheck[f.check] ??= { total: 0, open: 0 }; byCheck[f.check].total++; if (!f.allowlisted) byCheck[f.check].open++; }
const roles = [...new Set(units.map((u) => u.unit.role))];
const themes = [...new Set(units.map((u) => u.unit.theme))];
const visits = units.reduce((n, u) => n + u.coverage.length, 0);
const layers = units.reduce((n, u) => n + u.coverage.reduce((m, c) => m + c.layers, 0), 0);
const summary = {
  when: now.toISOString(), mode: quick ? 'quick' : 'full', hub: process.env.HUB || 'http://localhost:8080', seconds: secs, playwrightStatus: pwStatus,
  routes: routes.routes.length, roles, themes, units: units.map((u) => u.unit.name), pageVisits: visits, layersOpened: layers,
  findings: findings.length, open: open.length, byCheck,
};
fs.writeFileSync(path.join(out, 'findings.json'), JSON.stringify({ summary, findings }, null, 2));

// ---- Markdown: achados agrupados (mesma rota/verificação/seletor/mensagem em vários temas/papéis = 1 linha) ----
const groups = new Map();
for (const f of open) {
  const k = [f.pattern, f.layer, f.check, f.selector, f.message].join('¦');
  const g = groups.get(k) || { ...f, combos: new Set(), shot: f.screenshot };
  g.combos.add(`${f.role}/${f.theme}/${f.viewport.split('x')[0]}`);
  g.shot ||= f.screenshot;
  groups.set(k, g);
}
const SEV = ['http-status', 'sem-acesso-fora-do-shell', 'fora-do-shell', 'erro-de-pagina', 'console', 'cor-igual-fundo', 'select-vazio', 'menu-vazio', 'controle-escuro', 'jargao', 'sem-nome-acessivel', 'contraste', 'link-sublinhado', 'icone-desalinhado', 'estouro-horizontal', 'estouro-interno', 'camada-sem-esc', 'camada-sem-fechar', 'modal-fecha-no-fundo', 'gatilho-sem-efeito', 'imagem-sem-alt', 'foco-invisivel', 'truncado-sem-dica', 'contraste-ui', 'navegacao'];
const rank = (c) => { const i = SEV.indexOf(c); return i < 0 ? 99 : i; };
const sorted = [...groups.values()].sort((a, b) => rank(a.check) - rank(b.check) || b.combos.size - a.combos.size || a.pattern.localeCompare(b.pattern));
const esc = (s) => String(s ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ');
const md = [];
md.push(`# Varredura de UI — ${summary.when.slice(0, 19).replace('T', ' ')}`, '');
md.push(`- Modo: **${summary.mode}** · hub ${summary.hub} · duração ${Math.floor(secs / 60)}min${secs % 60}s · Playwright saiu com ${pwStatus}`);
md.push(`- Cobertura: **${summary.routes} rotas × ${themes.length} temas (${themes.join(', ')}) × ${roles.length} papéis (${roles.join(', ')})** — ${units.length} unidades, ${visits} visitas de página, ${layers} camadas abertas (menus/popovers/modais)`);
md.push(`- Achados: **${findings.length}** (${open.length} fora da allowlist; ${groups.size} distintos após agrupar temas/papéis)`, '');
md.push('## Resumo por verificação', '', '| Verificação | Achados | Fora da allowlist |', '|---|---:|---:|');
for (const [c, v] of Object.entries(byCheck).sort((a, b) => rank(a[0]) - rank(b[0]))) md.push(`| ${c} | ${v.total} | ${v.open} |`);
md.push('', '## Achados (agrupados)', '', '| # | Rota | Camada | Verificação | Seletor | Mensagem | Onde (papel/tema/largura) | Recorte |', '|---:|---|---|---|---|---|---|---|');
sorted.forEach((g, i) => md.push(`| ${i + 1} | \`${esc(g.pattern)}\` | ${esc(g.layer)} | ${g.check} | \`${esc(g.selector).slice(0, 90)}\` | ${esc(g.message)} | ${[...g.combos].join(', ')} | ${g.shot ? `[png](${g.shot})` : ''} |`));
md.push('', '## Rotas cobertas', '', '| Padrão | Exemplo | Origem | Status por unidade |', '|---|---|---|---|');
for (const r of routes.routes) {
  const st = units.map((u) => { const c = u.coverage.find((x) => x.pattern === r.pattern); return c ? `${u.unit.name}:${c.status}` : null; }).filter(Boolean);
  const uniq = [...new Set(st.map((s) => s.split(':')[1]))];
  md.push(`| \`${r.pattern}\` | ${r.path} | ${r.from === 'tabela-de-rotas' ? 'tabela de rotas' : 'link'} | ${uniq.length === 1 ? 'todas: ' + uniq[0] : st.join(' ')} |`);
}
if (routes.excluded?.length) md.push('', `Links excluídos (saída/destrutivos/downloads/API): ${routes.excluded.map((x) => '`' + x + '`').join(', ')}`);
const allowedList = findings.filter((f) => f.allowlisted);
if (allowedList.length) md.push('', `## Na allowlist (${allowedList.length})`, '', ...allowedList.slice(0, 200).map((f) => `- \`${f.pattern}\` ${f.check} — ${esc(f.message)} — _${f.allowlisted}_`));
fs.writeFileSync(path.join(out, 'report.md'), md.join('\n') + '\n');

console.log(`\n[ui-sweep] ${summary.routes} rotas × ${themes.length} temas × ${roles.length} papéis · ${visits} visitas · ${layers} camadas · ${secs}s`);
for (const [c, v] of Object.entries(byCheck)) console.log(`  ${c.padEnd(26)} ${v.open}/${v.total}`);
console.log(`[ui-sweep] ${open.length} achado(s) fora da allowlist → ${path.join(out, 'report.md')}`);
process.exit(pwStatus !== 0 ? 2 : open.length ? 1 : 0);
