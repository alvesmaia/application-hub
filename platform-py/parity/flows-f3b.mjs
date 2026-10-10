// Fluxos de UI de Observabilidade/Operação (F3b) no hub Python com o JS original (admin.js, insights.js, ux.js).
// Uso: node platform-py/parity/flows-f3b.mjs   (PY_URL, HUB_DEMO_PASSWORD como no compare.mjs)
// Cria e remove os próprios dados (prefixo f3b-*); só toca o hub Python.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire('C:/PROJETOS/PROD/application-hub/platform/tests-e2e/package.json');
const { chromium } = require('playwright');
const BASE = process.env.PY_URL || 'http://localhost:8090';
const env = Object.fromEntries(fs.readFileSync(path.join(here, '../.dev.env'), 'utf8').split(/\r?\n/).filter((l) => l.includes('=')).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const PASS = process.env.HUB_DEMO_PASSWORD || env.HUB_DEMO_PASSWORD;
const id = Date.now().toString(36);
const shots = path.resolve(here, '../dist/flows-f3b');
fs.mkdirSync(shots, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light', locale: 'pt-BR' });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`); });
let fails = 0;
const ok = (cond, what) => { console.log(`${cond ? 'ok  ' : 'FAIL'} ${what}`); if (!cond) fails++; };
const api = (method, url, body) => page.evaluate(async ([m, u, b]) => {
  const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
  const t = await r.text();
  let data = null;
  try { data = t ? JSON.parse(t) : null; } catch { data = t; }
  return { status: r.status, body: data };
}, [method, url, body]);
const go = async (p) => { await page.goto(BASE + p, { waitUntil: 'networkidle' }); };
const afterReload = async (action) => { await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle' }), action()]); };
const modal = '.overlay[data-insights-modal]';

await page.goto(BASE + '/login');
await page.fill('#email', 'admin');
await page.fill('#password', PASS);
await Promise.all([page.waitForLoadState('load'), page.press('#password', 'Enter')]);

// ---- feature flags -------------------------------------------------------------------------
const key = `f3b-${id}`;
await go('/admin/flags');
await page.click('[data-flag-new]');
await page.fill(`${modal} input[name=key]`, key);
await page.selectOption(`${modal} select[name=app]`, 'ativos');
await page.fill(`${modal} input[name=description]`, 'fluxo F3b');
await afterReload(() => page.click(`${modal} button[type=submit]`));
const frow = () => page.locator(`tr[data-flag-row="${key}"]`);
ok(await frow().count() === 1, 'flag criada aparece na tabela');
await afterReload(() => frow().locator('[data-flag-toggle=producao]').click());
ok((await frow().innerText()).includes('Desligar (kill switch)'), 'ligar em produção (kill switch passa a oferecer desligar)');
await frow().locator('[data-flag-rules=producao]').click();
await page.fill(`${modal} input[name=percent]`, '40');
await afterReload(() => page.click(`${modal} button[type=submit]`));
let fl = (await api('GET', '/api/admin/flags')).body.flags.find((f) => f.key === key);
ok(fl && fl.envs.producao.enabled && fl.envs.producao.rollout.percent === 40, 'regra de percentual salva (40%)');
ok((await frow().innerText()).includes('40% → on'), 'resumo da regra na tabela');
let r = await api('POST', `/api/admin/flags/ativos/${key}/producao/enabled`, { enabled: 'sim' });
ok(r.status === 400, 'enabled inválido recusado');
await page.screenshot({ path: path.join(shots, 'flags.png') });
await frow().locator('[data-flag-delete]').click();
await afterReload(() => page.getByRole('button', { name: 'Confirmar' }).click());
ok(await frow().count() === 0, 'flag excluída');

// ---- SLO + incidentes + status ---------------------------------------------------------------
await go('/admin/health');
await page.locator('button[data-slo-edit=""]').click();
await page.selectOption(`${modal} select[name=app]`, 'servicos');
await page.fill(`${modal} input[name=latency_ms]`, '900');
await afterReload(() => page.click(`${modal} button[type=submit]`));
ok(await page.locator('[data-slo-table] tbody tr:has-text("Serviços")').count() === 1, 'SLO definido aparece (Serviços)');
ok((await page.locator('[data-slo-table] tbody tr:has-text("Serviços")').innerText()).includes('900 ms'), 'meta do SLO na tabela');
await page.locator('[data-slo-table] tbody tr:has-text("Serviços") [data-slo-edit]').click();
await afterReload(() => page.getByRole('button', { name: 'Remover SLO' }).click());
ok(await page.locator('[data-slo-table] tbody tr:has-text("Serviços")').count() === 0, 'SLO removido');

const title = `Instabilidade ${id}`;
await go('/status');
await page.click('[data-incident-new]');
await page.fill(`${modal} input[name=title]`, title);
await page.selectOption(`${modal} select[name=severity]`, 'minor');
await afterReload(() => page.click(`${modal} button[type=submit]`));
const inc = () => page.locator(`.incident:has-text("${title}")`);
ok(await inc().count() === 1, 'incidente registrado na status page');
ok((await page.locator('[data-status-overall]').innerText()).includes('problemas') || (await page.locator('[data-status-overall]').innerText()).includes('indisponíveis'), 'banner geral reflete incidente aberto');
await inc().locator('[data-incident-note]').click();
await page.fill(`${modal} textarea[name=note]`, 'investigando');
await afterReload(() => page.click(`${modal} button[type=submit]`));
ok((await inc().innerText()).includes('investigando'), 'nota adicionada');
await inc().locator('[data-incident-resolve]').click();
await afterReload(() => page.click(`${modal} button[type=submit]`));
ok((await inc().innerText()).includes('Resolvido'), 'incidente resolvido');
await page.screenshot({ path: path.join(shots, 'status.png') });

// ---- saúde ------------------------------------------------------------------------------------
const svc = `F3b ${id}`;
await go('/admin/health');
await page.click('[data-admin-add=health]');
await page.fill('.overlay input[data-field=name]', svc);
await afterReload(() => page.click('.overlay button[type=submit]'));
ok(await page.locator(`tr[data-item]:has-text("${svc}")`).count() === 1, 'serviço monitorado criado');
r = await api('PUT', `/api/admin/health/${encodeURIComponent(svc)}`, { kind: 'Serviço', status: 'Manutenção', app: null });
ok(r.status === 200 && r.body.status === 'Manutenção', 'serviço atualizado');
r = await api('POST', '/api/admin/health/check', {});
ok(r.status === 200 && Array.isArray(r.body.results) && r.body.results.length >= 1, 'Verificar agora devolve a sonda de cada app');
r = await api('DELETE', `/api/admin/health/${encodeURIComponent(svc)}`);
ok(r.status === 204, 'serviço removido');

// ---- auditoria ---------------------------------------------------------------------------------
await go('/admin/audit');
await page.click('[data-audit-verify]');
await page.waitForSelector('[data-audit-verify-result]:not(.hidden)');
ok((await page.locator('[data-audit-verify-result]').innerText()).includes('Íntegra'), 'verificar integridade: cadeia íntegra');
await page.screenshot({ path: path.join(shots, 'audit.png') });
const [dl] = await Promise.all([page.waitForEvent('download'), page.click('[data-audit-export]')]);
const text = fs.readFileSync(await dl.path(), 'utf8').trim().split('\n');
ok(dl.suggestedFilename() === 'auditoria.jsonl' && JSON.parse(text.at(-1)).t === 'proof', 'exportação JSONL termina com a prova');
await go('/admin/audit?scope=ativos');
ok(await page.locator('.filter-chip.active', { hasText: 'Ativos' }).count() === 1, 'filtro por aplicação');

// ---- logs (consulta e tempo real) -------------------------------------------------------------
await go('/admin/logs');
await page.selectOption('[data-log-source]', 'admin');
await page.click('[data-log-query]');
await page.waitForTimeout(500);
ok(await page.locator('.log-line').count() > 0, 'consulta de logs por origem devolve linhas');
await page.click('[data-log-live]');
await page.waitForFunction(() => /Ao vivo|Tempo real|Conectado/i.test(document.querySelector('[data-log-status]').textContent), null, { timeout: 5000 }).catch(() => {});
await api('PUT', '/api/admin/slos', { app: 'ativos', env: 'producao', availability: 99.5, latency_ms: 800, latency_target: 95, window_days: 30 });
await page.waitForFunction(() => document.querySelector('[data-log-terminal]').textContent.includes('SLO definido'), null, { timeout: 8000 }).then(() => ok(true, 'tempo real: nova linha chega pelo SSE'), () => ok(false, 'tempo real: nova linha chega pelo SSE'));
await page.screenshot({ path: path.join(shots, 'logs.png') });

// ---- observabilidade --------------------------------------------------------------------------
await page.goto(BASE + '/_frag/ativos/dashboard').catch(() => {});
await go('/admin/observability?app=ativos&min=15');
ok(await page.locator('.obs-traces tbody tr').count() >= 1, 'trace do gateway aparece nas requisições mais lentas');
await page.locator('[data-obs-trace]').first().click();
await page.waitForLoadState('networkidle');
ok(await page.locator('.obs-waterfall .obs-span').count() >= 1, 'cascata do trace');
await page.screenshot({ path: path.join(shots, 'observability.png') });

// ---- analytics --------------------------------------------------------------------------------
await go('/admin/analytics');
await page.fill('#an-days', '60');
await page.click('[data-retention-form] button');
await page.waitForTimeout(800);
r = await api('GET', '/api/admin/analytics');
ok(r.status === 200 && r.body.retention_days === 60, 'retenção salva pelo formulário');
await api('PUT', '/api/admin/analytics/retention', { days: 90 });
ok((await (await page.request.get(BASE + '/api/admin/analytics.csv')).text()).includes('dia;usuarios_ativos'), 'exportar CSV');
await page.screenshot({ path: path.join(shots, 'analytics.png') });

ok(errors.length === 0, `sem erros de console (${errors.slice(0, 3).join(' | ')})`);
await browser.close();
console.log(fails ? `${fails} FALHA(S)` : 'tudo ok');
process.exit(fails ? 1 : 0);
