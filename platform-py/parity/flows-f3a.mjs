// Fluxos de UI da Administração (F3a) no hub Python com o JS original (users.js, admin.js, connections.js).
// Uso: node platform-py/parity/flows-f3a.mjs   (PY_URL, HUB_DEMO_PASSWORD como no compare.mjs)
// Cria e remove os próprios dados (prefixo f3a-*); só toca o hub Python.
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
const shots = path.resolve(here, '../dist/flows-f3a');
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
  return { status: r.status, body: t ? JSON.parse(t) : null };
}, [method, url, body]);
const go = async (p) => { await page.goto(BASE + p, { waitUntil: 'networkidle' }); };
const afterReload = async (action) => { await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle' }), action()]); };

await page.goto(BASE + '/login');
await page.fill('#email', 'admin');
await page.fill('#password', PASS);
await Promise.all([page.waitForLoadState('load'), page.press('#password', 'Enter')]);

// ---- usuários ---------------------------------------------------------------------------
const email = `f3a-${id}@demo.local`;
await go('/admin/users');
await page.click('[data-user-new]');
await page.screenshot({ path: path.join(shots, 'users-new-modal.png') });
await page.fill('input[name=name]', `F3a ${id}`);
await page.fill('input[name=email]', email);
await afterReload(() => page.click('.overlay[data-users-modal] button[type=submit]'));
const row = () => page.locator(`tr[data-user-id]:has-text("${email}")`);
ok(await row().count() === 1, 'usuário criado aparece na tabela');
let u = (await api('GET', '/api/admin/users')).body.find((x) => x.email === email);
ok(u && u.must_change_password && u.roles[0] === 'Colaborador', 'novo usuário: troca de senha pendente e função Colaborador');
ok((await row().innerText()).includes('Troca de senha pendente'), 'status "Troca de senha pendente"');
await row().locator('button[data-user-edit]').first().click();
await page.fill('.overlay[data-users-modal] input[name=name]', `F3a editado ${id}`);
await afterReload(() => page.click('.overlay[data-users-modal] button[type=submit]'));
u = (await api('GET', '/api/admin/users')).body.find((x) => x.email === email);
ok(u.name === `F3a editado ${id}`, 'editar nome');
await row().locator('summary').click();
await row().locator('button[data-user-reset]').click();
await afterReload(() => page.click('.overlay[data-users-modal] button[type=submit]'));
ok(true, 'redefinir senha (modal e envio)');
await row().locator('summary').click();
await row().locator('button[data-user-toggle]').click();
await afterReload(() => page.getByRole('button', { name: 'Confirmar' }).click());
u = (await api('GET', '/api/admin/users')).body.find((x) => x.email === email);
ok(u.active === false && (await row().innerText()).includes('Desativado'), 'desativar (confirmação) e status "Desativado"');
await page.setViewportSize({ width: 390, height: 844 });
await page.screenshot({ path: path.join(shots, 'users-mobile-cards.png') });
ok(await page.locator('table.hub-table-cards tr[data-user-id]').first().evaluate((el) => getComputedStyle(el).display) !== 'table-row', 'mobile: linhas viram cartões');
await page.setViewportSize({ width: 1440, height: 900 });
ok((await api('DELETE', `/api/admin/users/${u.id}`)).status === 204, 'excluir usuário desativado');

// ---- funções ----------------------------------------------------------------------------
const role = `F3a ${id}`;
await go('/admin/roles');
await page.click('[data-admin-add="role"]');
await page.fill('[data-field=name]', role);
await page.fill('[data-field=description]', 'teste e2e');
await afterReload(() => page.click('.overlay button[type=submit]'));
const card = () => page.locator(`.role-card[data-role="${role}"]`);
ok(await card().count() === 1, 'função criada aparece');
await card().locator('[data-role-toggle]').click();
await Promise.all([page.waitForResponse((r) => r.url().includes('/permissions/3') && r.ok()), card().locator('[data-role-perm="3"]').check()]);
let r = (await api('GET', '/api/admin/roles')).body.find((x) => x.name === role);
ok(r.permissions[3] === true, 'marcar permissão salva sozinha');
await Promise.all([page.waitForResponse((x) => x.url().includes('/apps') && x.ok()), card().locator('[data-role-app-all]').uncheck()]);
r = (await api('GET', '/api/admin/roles')).body.find((x) => x.name === role);
ok(Array.isArray(r.apps), 'restringir aplicações da função');
await api('DELETE', `/api/admin/roles/${encodeURIComponent(role)}`);

// ---- variáveis --------------------------------------------------------------------------
const VAR = `F3A_${id.toUpperCase()}`;
await go('/admin/env');
await page.click('[data-admin-add="env"]');
await page.selectOption('[data-field=kind]', 'secreta');
await page.fill('[data-field=name]', VAR);
await page.fill('[data-field=value]', 'valor-secreto-e2e-12345');
await page.check('[data-field-binding][data-app="ativos"][data-env="staging"]');
await afterReload(() => page.click('.overlay button[type=submit]'));
const vrow = () => page.locator(`tr:has(code.env-key:text-is("${VAR}"))`);
ok(await vrow().count() === 1, 'variável criada aparece');
ok((await vrow().innerText()).includes('••••••••2345') && !(await page.content()).includes('valor-secreto-e2e'), 'segredo mascarado no HTML');
ok(await vrow().locator('[data-env-reveal]').count() === 0, 'segredo não tem controle de revelar');
ok((await page.locator('article[data-env-app="ativos"]').innerText()).includes('Reinício pendente'), 'card do app: reinício pendente');
await page.screenshot({ path: path.join(shots, 'env.png') });
await api('DELETE', `/api/admin/env/${VAR}`);

// ---- conexões ---------------------------------------------------------------------------
await go('/admin/connections');
await page.click('[data-conn-new-menu] summary');
await page.click('[data-conn-new="sharepoint"]');
await page.screenshot({ path: path.join(shots, 'connections-new-sharepoint.png') });
await page.fill('[data-field=name]', `f3a-sp-${id}`);
await page.fill('[data-field=tenant_id]', 'contoso.onmicrosoft.com');
await page.fill('[data-field=client_id]', '11111111-2222-3333-4444-555555555555');
await page.selectOption('[data-field=auth]', 'secret');
await page.fill('[data-field=secret]', 'segredo-do-cliente-e2e');
await page.fill('[data-field=site]', 'https://contoso.sharepoint.com/sites/f3a');
await afterReload(() => page.click('.overlay button[type=submit], [data-conn-modal] button[type=submit]'));
const crow = (n) => page.locator(`tr[data-conn-row="${n}"]`);
ok(await crow(`f3a-sp-${id}`).count() === 1 && !(await page.content()).includes('segredo-do-cliente-e2e'), 'conexão SharePoint criada, segredo fora do HTML');
await api('DELETE', `/api/admin/connections/f3a-sp-${id}`);
// provisionar armazenamento (MinIO do hub Python), testar e desprovisionar
await go('/admin/connections');
await page.selectOption('[data-conn-storage-app]', 'servicos');
await page.selectOption('[data-conn-storage-env]', 'staging');
await afterReload(() => page.click('[data-conn-storage-provision]'));
ok(await page.locator('[data-conn-storage-row="servicos|staging"]').count() === 1 && await crow('stg-servicos').count() === 1, 'armazenamento provisionado: bucket + conexão');
await page.click('[data-conn-test="stg-servicos"]');
await page.waitForFunction(() => document.querySelector('[data-conn-status="stg-servicos"]')?.textContent.includes('OK'), null, { timeout: 15000 });
ok(true, 'testar conexão (MinIO real): todas as etapas passaram');
await page.screenshot({ path: path.join(shots, 'connections-provisioned.png') });
r = await api('POST', '/api/admin/connections/storage/servicos/deprovision?env=staging', { confirm: 'stg-servicos' });
ok(r.status === 200 && r.body.removed, 'desprovisionar remove conexão, usuário e bucket');

ok(errors.length === 0, `sem erros de console/página${errors.length ? ': ' + errors.join(' | ') : ''}`);
await browser.close();
console.log(fails ? `${fails} falha(s)` : 'todos os fluxos passaram');
process.exit(fails ? 1 : 0);
