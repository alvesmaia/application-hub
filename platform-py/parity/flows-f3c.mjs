// Fluxos de UI de Operações (F3c) no hub Python com o JS original (ops.js): implantações, containers, tarefas, backups.
// Uso: node platform-py/parity/flows-f3c.mjs   (PY_URL, HUB_DEMO_PASSWORD como no compare.mjs; hub Python com Docker real e app-hub-app-db no ar)
// Mexe só em app-hub-* (restart do app-hub-app-ativos, backup do banco `hub` do app-hub-app-db); nunca toca o hub Rust.
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire('C:/PROJETOS/PROD/application-hub/platform/tests-e2e/package.json');
const { chromium } = require('playwright');
const BASE = process.env.PY_URL || 'http://localhost:8091';
const env = Object.fromEntries(fs.readFileSync(path.join(here, '../.dev.env'), 'utf8').split(/\r?\n/).filter((l) => l.includes('=')).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const PASS = process.env.HUB_DEMO_PASSWORD || env.HUB_DEMO_PASSWORD;
const shots = path.resolve(here, '../dist/flows-f3c');
fs.mkdirSync(shots, { recursive: true });

// pacotes .hubapp de teste (gerados com o helper dos testes pytest)
const mk = (name, kw) => {
  const out = path.join(shots, name);
  execFileSync('uv', ['run', 'python', '-c', `import sys; sys.path.insert(0,'tests'); import test_ops, pathlib; pathlib.Path(r'${out}').write_bytes(test_ops.package(${kw}))`],
    { cwd: path.resolve(here, '..'), stdio: 'inherit' });
  return out;
};
const pkgOk = mk('ok.hubapp', '');
const pkgSecret = mk('secret.hubapp', 'secret=True');
const pkgScripts = mk('scripts.hubapp', 'scripts=True');

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
const reloaded = async (action) => { await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle', timeout: 60000 }), action()]); };
const audit = async () => (await api('GET', '/api/admin/audit?limit=60')).body.map((e) => e.title);

await page.goto(BASE + '/login');
await page.fill('#email', 'admin');
await page.fill('#password', PASS);
await Promise.all([page.waitForLoadState('load'), page.press('#password', 'Enter')]);

// ---- Implantações: importar (validar pipeline), rejeitar, revisão de scripts -------------------------
await go('/admin/deploys');
await page.selectOption('#ops-env', 'producao');
await page.setInputFiles('[data-ops-file]', pkgOk);
ok((await page.textContent('[data-ops-file-name]')).includes('ok.hubapp'), 'arquivo escolhido aparece na área de envio');
await page.click('[data-ops-import]');
await page.waitForFunction(() => document.querySelector('[data-ops-pipeline-status]').textContent.includes('Aguardando aprovação'), null, { timeout: 30000 });
ok((await page.locator('[data-ops-steps] > li.done').count()) >= 4, 'passos do pipeline pintados como concluídos');
await page.screenshot({ path: path.join(shots, 'deploys-pipeline.png') });
await page.waitForNavigation({ waitUntil: 'networkidle' });
ok((await page.locator('tbody tr:has-text("novoapp") .ops-pill-warn').count()) >= 1, 'implantação aguardando aprovação na tabela');

await page.setInputFiles('[data-ops-file]', pkgSecret);
await page.click('[data-ops-import]');
await page.waitForFunction(() => document.querySelector('[data-ops-pipeline-status]').textContent.includes('Rejeitado'), null, { timeout: 30000 });
ok((await page.locator('[data-ops-steps] > li.failed').count()) === 1, 'pacote com segredo reprovado no passo Segurança');
await page.waitForNavigation({ waitUntil: 'networkidle' });
ok((await page.locator('.ops-pill-err:has-text("Rejeitado")').count()) >= 1, 'rejeição registrada na tabela');

await page.setInputFiles('[data-ops-file]', pkgScripts);
await page.click('[data-ops-import]');
await page.waitForNavigation({ waitUntil: 'networkidle', timeout: 30000 });
const review = page.locator('.ops-revise').first();
ok(await review.isVisible(), 'revisão de segurança do JavaScript próprio visível');
await page.screenshot({ path: path.join(shots, 'deploys-scripts.png') });
page.once('dialog', (d) => d.accept());
await review.locator('[data-ops-approve-scripts]').click();
await page.waitForTimeout(800);
const dep = (await api('GET', '/api/ops/deployments')).body.find((d) => d.scripts?.length);
const r = await api('POST', `/api/ops/deployments/${dep.id}/scripts/approve`);
ok(r.status === 400 && r.body.error.includes('outra pessoa'), 'quem enviou não aprova o próprio JavaScript (separação de funções)');
const r2 = await api('POST', `/api/ops/deployments/${dep.id}/approve`);
ok(r2.status === 400 && r2.body.error.includes('JavaScript próprio'), 'aprovar a implantação antes da revisão é recusado');
const dOk = (await api('GET', '/api/ops/deployments')).body.find((d) => d.status === 'Aguardando aprovação' && !d.scripts?.length && d.app === 'novoapp');
const r3 = await api('POST', `/api/ops/deployments/${dOk.id}/approve`);
ok(r3.status === 503 && r3.body.error.includes('F5'), 'aprovar ativação: 503 explícito (blue/green é da F5)');
const t1 = await audit();
ok(t1.includes('Pacote validado — aguardando aprovação') && t1.includes('Pacote rejeitado pelo pipeline de validação') && t1.includes('Aprovação de JavaScript próprio recusada'), 'auditoria das escritas de implantação');

// ---- Containers: drawer (logs, inspect), limites, reiniciar ---------------------------------------------
await go('/admin/runtime');
await page.screenshot({ path: path.join(shots, 'runtime.png') });
const row = page.locator('.ops-ct-row[data-ops-app=ativos]');
ok(await row.count() === 1 && (await row.textContent()).includes('app-hub-app-ativos'), 'linha do contêiner app-hub-app-ativos');
ok((await row.locator('[data-ops-state=ok]').count()) === 1, 'estado Em execução (Docker real)');
await row.locator('summary').click();
await row.locator('[data-ops-open="ativos|producao|logs"]').click();
await page.waitForTimeout(2500);
ok((await page.locator('[data-ops-detail="ativos|producao"] [data-ops-logs]').textContent()).trim().length > 0, 'aba Logs mostra a saída real do contêiner');
await page.click('[data-ops-tab=inspect]');
await page.waitForTimeout(1200);
const insp = await page.locator('[data-ops-detail="ativos|producao"] [data-ops-inspect]').textContent();
ok(insp.includes('"Config"') && !/HUB_INTERNAL_TOKEN=[^•]/.test(insp), 'aba Inspect traz o inspect com variáveis ocultas');
await page.screenshot({ path: path.join(shots, 'runtime-drawer.png') });
await page.click('[data-ops-drawer-close]');
await row.locator('summary').click();
await row.locator('[data-ops-limits]').click();
await page.fill('#ops-f-pids', '150');
await reloaded(() => page.click('.overlay button[type=submit]'));
ok((await api('GET', '/api/ops/runtime')).body.apps.find((a) => a.id === 'ativos').pids === 150, 'limites salvos pelo modal');
await api('PUT', '/api/ops/runtime/ativos/limits', { cpu: 1, memory: '1Gi', pids: 100 });
const row2 = page.locator('.ops-ct-row[data-ops-app=ativos]');
await row2.locator('summary').click();
await reloaded(() => row2.locator('[data-ops-container=restart]').click());
await page.waitForTimeout(3000);
ok(execFileSync('docker', ['inspect', '-f', '{{.State.Running}}', 'app-hub-app-ativos']).toString().trim() === 'true', 'app-hub-app-ativos reiniciado e rodando');
const t2 = await audit();
ok(t2.includes('Limites de container alterados') && t2.includes('Container reiniciado'), 'auditoria de limites e reinício');

// ---- Tarefas ---------------------------------------------------------------------------------------------
await go('/admin/tasks');
ok(await page.locator('#tasks-table[data-hub-colfilter]').count() === 1, 'tabela de tarefas com filtros por coluna');
await page.screenshot({ path: path.join(shots, 'tasks.png') });
const tk = await api('GET', '/api/ops/tasks');
ok(tk.status === 200 && Array.isArray(tk.body.tasks), 'API de tarefas (lista, com erros por app inacessível)');

// ---- Backups: política, backup agora, teste de restauração, credenciais ---------------------------------
const psql = (db, sql) => execFileSync('docker', ['exec', 'app-hub-app-db', 'psql', '-U', 'hub', '-d', db, '-Atc', sql]).toString().trim();
psql('hub', 'drop database if exists app_coletor');
psql('hub', 'create database app_coletor');
psql('app_coletor', "create table f3c(id int); insert into f3c values (1),(2),(3)");
ok(psql('app_coletor', 'select count(*) from f3c') === '3', 'banco app_coletor de teste criado no app-hub-app-db');
await go('/admin/backups');
const pol = page.locator('tr:has-text("Coletor") [data-ops-policy]');
await pol.click();
await page.fill('#ops-f-keep', '2');
await page.fill('#ops-f-volumes', 'data');
await reloaded(() => page.click('.overlay button[type=submit]'));
ok((await api('GET', '/api/ops/backups')).body.policies.find((p) => p.app === 'coletor').keep === 2, 'política editada pelo modal');
await page.click('tr:has-text("Coletor") [data-ops-backup=coletor]');
await page.waitForSelector('[data-ops-progress]:not([hidden])', { timeout: 10000 });
await page.waitForFunction(() => document.querySelector('[data-ops-progress-bar]').style.width === '100%', null, { timeout: 90000 });
await page.waitForNavigation({ waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
const bk = (await api('GET', '/api/ops/backups')).body.history.find((b) => b.app === 'coletor');
ok(bk.status === 'Concluído' && bk.size > 0 && bk.detail.includes('banco app_coletor'), `backup concluído (${bk.detail})`);
await page.screenshot({ path: path.join(shots, 'backups.png') });
const roles = execFileSync('docker', ['exec', 'app-hub-app-db', 'psql', '-U', 'hub', '-Atc', "select rolname, rolsuper, rolcanlogin from pg_roles where rolname='hub_bkp_app_coletor'"]).toString().trim();
ok(roles === 'hub_bkp_app_coletor|f|t', 'papel hub_bkp_app_coletor criado sem superusuário (privilégio mínimo)');
page.once('dialog', (d) => d.accept());
const test = await api('POST', `/api/ops/backups/records/${bk.id}/test`);
ok(test.status === 202, 'teste de restauração enfileirado');
let job;
for (let i = 0; i < 90; i++) { job = (await api('GET', `/api/ops/jobs/${test.body.id}`)).body; if (job.done) break; await page.waitForTimeout(1000); }
ok(job.ok, `teste de restauração em banco temporário (${job.message})`);
ok(!execFileSync('docker', ['exec', 'app-hub-app-db', 'psql', '-U', 'hub', '-Atc', "select datname from pg_database where datname like 'app_coletor_rt%'"]).toString().trim(), 'banco temporário removido');
await go('/admin/backups');
await page.click('[data-ops-backup-creds]');
await page.click('.hub-confirm button.primary-button, [data-hub-confirm-ok], .overlay .primary-button').catch(() => {});
await page.waitForTimeout(2500);
const t3 = await audit();
ok(t3.includes('Backup concluído') && t3.includes('Teste de restore concluído'), 'auditoria de backup e teste de restore');
ok((await api('POST', '/api/ops/backups/aside/x/drop')).status === 404, 'bancos de lado: nada a apagar (restauração é transacional)');

psql('hub', 'drop database if exists app_coletor');
execFileSync('docker', ['exec', 'app-hub-app-db', 'psql', '-U', 'hub', '-d', 'hub', '-c', 'drop role if exists hub_bkp_app_coletor']);
ok(errors.length === 0, `sem erros de console (${errors.slice(0, 3).join(' | ')})`);
await browser.close();
console.log(fails ? `${fails} FALHA(S)` : 'tudo ok');
process.exit(fails ? 1 : 0);
