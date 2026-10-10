import { test, expect, type Page } from '@playwright/test';
import { apiAs, autoConfirm, docker, gotoSafe, json, newRoleContext, openApp, stateFile, uniq, CP } from './helpers';

// Onda 8: Observabilidade, status page e SLOs, feature flags, auditoria imutável,
// segredos cifrados em repouso e cor da marca (tokens).
test.use({ storageState: stateFile('admin') });
test.describe.configure({ mode: 'serial' });

const RUN = uniq();
const toast = (page: Page) => page.locator('#toast');

test('observabilidade na Saúde: gráficos por app e requisições mais lentas; link antigo redireciona', async ({ page }) => {
  // Tráfego: páginas do ativos (a lista consulta o banco).
  await openApp(page, '/apps/ativos/assets');
  await openApp(page, '/apps/ativos/dashboard');
  await page.goto('/admin/observability?app=ativos&env=producao&min=15');
  await expect(page).toHaveURL(/\/admin\/health\?app=ativos&env=producao&min=15#obs$/);
  await expect(page.locator('[data-obs-chart]')).toHaveCount(5);
  await expect(page.locator('[data-obs-chart="Requisições por segundo"] polyline')).toHaveCount(1);
  await expect(page.locator('.obs-traces')).toBeVisible();
});

test('SLO: definir na Saúde e ver orçamento de erro', async ({ page }) => {
  await page.goto('/admin/health?tab=slos'); // SLOs viraram aba da Saúde (F7a)
  await page.click('button[data-slo-edit=""]');
  const m = page.locator('[data-insights-modal] form');
  await m.locator('[name="app"]').selectOption('ativos');
  await m.locator('[name="env"]').selectOption('producao');
  await m.locator('[name="availability"]').fill('99.5');
  await m.locator('[name="latency_ms"]').fill('800');
  await m.locator('button[type="submit"]').click();
  await expect(toast(page)).toContainText('SLO salvo');
  const row = page.locator('[data-slo-table] tbody tr').filter({ hasText: 'Ativos' }).first();
  await expect(row).toContainText('99,5%');
  await expect(row.locator('.budget')).toBeVisible();
});

test('status page: estado dos apps, incidente manual com nota e resolução; leitor só lê', async ({ page, browser }) => {
  autoConfirm(page);
  await page.goto('/status');
  await expect(page.locator('[data-status-overall]')).toBeVisible();
  await expect(page.locator('[data-status-app]').first()).toBeVisible();
  await expect(page.locator('.uptime-bars').first().locator('span')).toHaveCount(30);
  const title = `Lentidão E2E ${RUN}`;
  await page.click('[data-incident-new]');
  const m = page.locator('[data-insights-modal] form');
  await m.locator('[name="app"]').selectOption('servicos');
  await m.locator('[name="title"]').fill(title);
  await m.locator('[name="severity"]').selectOption('minor');
  await m.locator('[name="note"]').fill('Investigando lentidão.');
  await m.locator('button[type="submit"]').click();
  await expect(toast(page)).toContainText('Incidente registrado');
  const inc = page.locator('[data-incident]').filter({ hasText: title });
  await expect(inc).toContainText('Em andamento');
  await expect(page.locator('[data-status-app="Central de Serviços"] [data-status-state]').first()).toHaveText('Degradado');
  // Leitor vê o incidente, sem botões de edição.
  const ctx = await newRoleContext(browser, 'leitor');
  const lp = await ctx.newPage();
  await lp.goto('/status');
  await expect(lp.locator('[data-incident]').filter({ hasText: title })).toBeVisible();
  await expect(lp.locator('[data-incident-new]')).toHaveCount(0);
  await ctx.close();
  await inc.locator('[data-incident-resolve]').click();
  await m.locator('[name="note"]').fill('Normalizado.');
  await m.locator('button[type="submit"]').click();
  await expect(toast(page)).toContainText('Incidente resolvido');
  await expect(page.locator('[data-incident]').filter({ hasText: title })).toContainText('Resolvido');
});

test('feature flags (só API; a tela foi removida): regra por função liga o recurso no app; kill switch desliga', async ({ page, browser }) => {
  test.setTimeout(180_000);
  const api = await apiAs('admin');
  // Estado limpo (de uma execução anterior).
  await api.delete('/api/admin/flags/ativos/novo-resumo', { data: {} });
  const rule = { enabled: true, default: 'off', rules: [{ roles: ['Administrador'], users: [], serve: 'on' }] };
  const created = await api.post('/api/admin/flags', { data: { key: 'novo-resumo', app: 'ativos', description: 'Resumo novo no dashboard (E2E)', kind: 'boolean', variants: [], envs: { producao: rule } } });
  expect(created.ok()).toBeTruthy();
  // A página antiga redireciona para Configurações.
  await page.goto('/admin/flags');
  await expect(page).toHaveURL(/[/]admin$/);
  // Admin (função Administrador) vê o recurso; leitor não.
  await openApp(page, '/apps/ativos/dashboard');
  await expect(page.locator('[data-flag="novo-resumo"]')).toBeVisible();
  const ctx = await newRoleContext(browser, 'leitor');
  const lp = await ctx.newPage();
  await openApp(lp, '/apps/ativos/dashboard');
  await expect(lp.locator('[data-flag="novo-resumo"]')).toHaveCount(0);
  await ctx.close();
  // Kill switch.
  expect((await api.post('/api/admin/flags/ativos/novo-resumo/producao/enabled', { data: { enabled: false } })).ok()).toBeTruthy();
  await openApp(page, '/apps/ativos/dashboard');
  await expect(page.locator('[data-flag="novo-resumo"]')).toHaveCount(0);
  // Auditado no escopo do app.
  const audit = await json(await api.get('/api/admin/audit?scope=ativos&limit=20'));
  expect(audit.map((e: { title: string }) => e.title)).toEqual(expect.arrayContaining(['Feature flag criada', 'Feature flag desligada (kill switch)']));
  await api.dispose();
});

test('auditoria imutável: verificar integridade e exportar JSONL com provas', async ({ page }) => {
  await page.goto('/admin/audit');
  await page.click('[data-audit-verify]');
  const out = page.locator('[data-audit-verify-result]');
  await expect(out).toContainText('Íntegra');
  const api = await apiAs('admin');
  const r = await api.get('/api/admin/audit/export');
  expect(r.status()).toBe(200);
  const lines = (await r.text()).trim().split('\n');
  const proof = JSON.parse(lines[lines.length - 1]);
  expect(proof.t).toBe('proof');
  expect(proof.verification.ok).toBe(true);
  expect(proof.public_keys.length).toBeGreaterThan(0);
  // checkpoint assinado a cada 100 eventos (F7a): numa instância nova pode ainda não haver nenhum
  const events = lines.filter((l) => l.includes('"t":"e"')).length;
  expect(lines.some((l) => l.includes('"t":"c"'))).toBe(events >= 100);
  await api.dispose();
});

test('segredos cifrados em repouso: o admin.json não tem o valor; o painel do cofre mostra a chave', async ({ page }) => {
  const api = await apiAs('admin');
  const name = `E2E_COFRE_${RUN}`;
  const value = `valor-secreto-${RUN}`;
  const r = await api.post('/api/admin/env', { data: { name, value, kind: 'secreta', bindings: [] } });
  expect(r.status(), await r.text()).toBe(201);
  // Conta no disco do hub (o admin.json é grande: grep dentro do container).
  const count = (needle: string) => Number(docker('exec', `${CP}web`, 'sh', '-c', `grep -c "${needle}" /data/admin.json || true`));
  expect(count(value)).toBe(0);
  expect(count('vault:v1:')).toBeGreaterThan(0);
  const back = await api.post(`/api/admin/env/${name}/reveal`, { data: {} });
  expect([403, 404, 405]).toContain(back.status()); // segredo nunca é revelado
  const v = await json(await api.get('/api/admin/vault'));
  expect(v.enabled).toBe(true);
  await gotoSafe(page, '/admin');
  await expect(page.locator('[data-vault-kek]')).toHaveText(v.kek_fingerprint);
  await api.delete(`/api/admin/env/${name}`, { data: {} });
  await api.dispose();
});

test('white label: cor da marca vira --color-accent em todas as telas', async ({ page }) => {
  const api = await apiAs('admin');
  const cur = await json(await api.get('/api/admin/settings'));
  try {
    const put = await api.put('/api/admin/settings', { data: { ...cur, brand_color: '#aa3300' } });
    expect(put.status(), await put.text()).toBe(200);
    await page.goto('/applications');
    const accent = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--color-accent').trim());
    expect(accent).toBe('#aa3300');
    const bad = await api.put('/api/admin/settings', { data: { ...cur, brand_color: 'red;}body{' } });
    expect(bad.status()).toBe(400);
  } finally {
    await api.put('/api/admin/settings', { data: cur });
    await api.dispose();
  }
});
