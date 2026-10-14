import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';
import { HUB, apiAs, json, openApp, stateFile, CP } from './helpers';

// Administration review batch: rail without counters, Containers in Docker Desktop style
// (list + details panel), "New connection ▾", column filters in Tasks and
// Logs only with real records. Stack up.
test.use({ storageState: stateFile('admin') });
const DIST = path.resolve(__dirname, '..', '..', 'dist');
const MOBILE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true };

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
}

test('rail sem contadores nos grupos @core', async ({ page }) => {
  await page.goto('/admin/runtime');
  await expect(page.locator('.rail-group-label').first()).toBeVisible();
  await expect(page.locator('.rail-group-count')).toHaveCount(0);
  await expect(page.locator('#hub-admin-menu')).not.toContainText(/\(\d+\)/);
});

test('containers: lista limpa e painel com variáveis mascaradas, logs e inspect @core', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin/runtime');
  const rows = page.locator('tr[data-ops-row]');
  await expect(rows.first()).toBeVisible();
  // At most one badge per row; one neutral icon for all.
  for (const r of await rows.all()) {
    expect(await r.locator('.status-pill').count()).toBeLessThanOrEqual(1);
    await expect(r.locator('.ops-ct-icon')).toHaveCount(1);
  }
  const bgs = await page.locator('.ops-ct-icon').evaluateAll((els) => [...new Set(els.map((e) => getComputedStyle(e).backgroundColor))]);
  expect(bgs.length).toBe(1);
  await page.screenshot({ path: path.join(DIST, 'runtime-list.png'), fullPage: true });
  const row = page.locator('tr[data-ops-row="ativos|producao"]');
  await expect(row).toContainText(`${CP}app-ativos`);
  await row.locator('td').first().click();
  const drawer = page.locator('[data-ops-drawer]');
  await expect(drawer).toBeVisible();
  await expect(drawer.locator('[data-ops-drawer-title]')).toHaveText(`${CP}app-ativos`);
  await expect(drawer.locator('.ops-detail:not([hidden]) .ops-facts')).toContainText('Porta interna');
  // Background does not close.
  await page.mouse.click(50, 450);
  await expect(drawer).toBeVisible();
  await drawer.getByRole('tab', { name: 'Variáveis vinculadas' }).click();
  const vars = drawer.locator('.ops-detail:not([hidden]) [data-ops-vars]');
  await expect(vars).toBeVisible();
  const parsed = JSON.parse((await vars.textContent()) || '{}');
  expect(typeof parsed).toBe('object');
  const env = await json(await (await apiAs('admin')).get('/api/admin/env'));
  const secrets = (Array.isArray(env) ? env : env.items || []).filter((v: { secret?: boolean; kind?: string }) => v.secret || /secret|token|senha/i.test(String(v.kind || '')));
  for (const [k, v] of Object.entries(parsed)) {
    if (secrets.some((s: { name: string }) => k.endsWith(s.name))) expect(v).toBe('••••');
  }
  await drawer.getByRole('tab', { name: 'Inspect' }).click();
  const insp = drawer.locator('.ops-detail:not([hidden]) [data-ops-inspect]');
  await expect(insp).toContainText('"Config"', { timeout: 30_000 });
  const ins = JSON.parse((await insp.textContent()) || '{}');
  for (const e of ins.Config.Env as string[]) expect(e).toMatch(/=••••$/);
  await drawer.getByRole('tab', { name: 'Logs' }).click();
  await expect(drawer.locator('[data-ops-logs-status]:visible')).not.toHaveText('Últimas linhas e novas em tempo real.', { timeout: 30_000 });
  await drawer.getByRole('tab', { name: 'Variáveis vinculadas' }).click();
  await page.screenshot({ path: path.join(DIST, 'runtime-details.png') });
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  // ⋯ → View logs opens directly on the tab.
  await row.locator('summary').click();
  await row.getByRole('menuitem', { name: 'Ver logs' }).click();
  await expect(drawer.getByRole('tab', { name: 'Logs' })).toHaveAttribute('aria-selected', 'true');
  await drawer.locator('[data-ops-drawer-close]').click();
  await expect(drawer).toBeHidden();
});

test('conexões: um botão "Nova conexão" com menu acessível @core', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin/connections');
  await expect(page.getByRole('button', { name: 'Nova conexão SharePoint' })).toHaveCount(0);
  const menu = page.locator('[data-conn-new-menu]');
  await menu.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(menu).toHaveAttribute('open', '');
  await page.keyboard.press('ArrowDown');
  await expect(menu.getByRole('menuitem', { name: 'SharePoint' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(menu.getByRole('menuitem', { name: 'Armazenamento S3' })).toBeFocused();
  await expect(menu.getByRole('menuitem', { name: 'Provisionar armazenamento' })).toBeVisible();
  await page.screenshot({ path: path.join(DIST, 'connections-dropdown.png') });
  await page.keyboard.press('Enter');
  await expect(page.locator('dialog[open], .modal:visible').first()).toBeVisible();
});

test('tarefas: filtros por coluna com estado na URL @core', async ({ page }) => {
  // Ensures at least one real task (export from Ativos).
  const api = await apiAs('admin');
  const has = async () => { const t = await json(await api.get('/api/ops/tasks')); return ((t.tasks || t.items || t) as unknown[]).length; };
  if (!(await has())) {
    await page.request.post('/_frag/ativos/itens/export.xlsx', { form: { sort: 'nome', dir: 'asc' }, headers: { origin: HUB } });
    await expect.poll(has, { timeout: 60_000 }).toBeGreaterThan(0);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin/tasks');
  await expect(page.locator('.filter-chips')).toHaveCount(0);
  const table = page.locator('#tasks-table');
  await expect(table.locator('[data-hub-cf-col]')).toHaveCount(5);
  const rows = table.locator('tbody tr');
  const total = await rows.count();
  test.skip(total === 0, 'sem tarefas no ambiente');
  const firstApp = (await rows.first().locator('td').nth(1).getAttribute('data-hub-value'))!;
  await table.locator('[data-hub-cf-col="app"]').click();
  const pop = page.locator('[data-hub-cf-pop="app"]');
  await expect(pop).toBeVisible();
  await expect(pop.getByText('Selecionar todos')).toBeVisible();
  await expect(pop.locator(`input[value="${firstApp}"]`)).toBeChecked();
  await page.screenshot({ path: path.join(DIST, 'tasks-filters.png') });
  await page.keyboard.press('Escape');
  // Date range: from tomorrow on there are no tasks.
  await table.locator('[data-hub-cf-col="data"]').click();
  const d = new Date(Date.now() + 86_400_000);
  const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  await page.locator('[data-hub-cf-pop="data"] input[type="date"]').first().fill(iso);
  await page.locator('[data-hub-cf-pop="data"]').getByRole('button', { name: 'Aplicar' }).click();
  await expect(page).toHaveURL(/f\.data=/);
  await expect(page.locator('[data-hub-cf-bar]')).toContainText('Filtros ativos');
  await expect(table.locator('[data-hub-cf-col="data"]')).toHaveClass(/on/);
  await expect(table.locator('tbody tr:visible')).toHaveCount(0);
  await expect(page.locator('.hub-cf-empty')).toBeVisible();
  // URL state is restored on reload; "Clear all" brings everything back.
  await page.reload();
  await expect(table.locator('tbody tr:visible')).toHaveCount(0);
  await page.getByRole('button', { name: 'Limpar tudo' }).click();
  await expect(page).not.toHaveURL(/f\.data=/);
  await expect(table.locator('tbody tr:visible')).toHaveCount(total);
  // Shared link with an app filter.
  await page.goto(`/admin/tasks?f.app=${firstApp}&sort=data:desc`);
  for (const r of await rows.all()) if (await r.isVisible()) await expect(r.locator('td').nth(1)).toHaveAttribute('data-hub-value', firstApp);
  await expect(table.locator('th[data-hub-col="data"]')).toHaveAttribute('aria-sort', 'descending');
});

test.describe('celular', () => {
  test.use(MOBILE);
  test('tarefas: "Filtros" abre folha de baixo para cima @core', async ({ page }) => {
    await page.goto('/admin/tasks');
    await page.getByRole('button', { name: 'Filtros' }).click();
    const sheet = page.locator('.hub-cf-sheet');
    await expect(sheet).toBeVisible();
    const box = (await sheet.boundingBox())!;
    expect(Math.round(box.y + box.height)).toBeGreaterThanOrEqual(840);
    await sheet.getByRole('button', { name: 'Estado' }).click();
    await expect(page.locator('.hub-cf-sheet[data-hub-cf-pop="status"]')).toBeVisible();
    await page.keyboard.press('Escape');
    await noOverflow(page);
  });
  test('containers: detalhes em tela cheia @core', async ({ page }) => {
    await page.goto('/admin/runtime');
    await page.locator('tr[data-ops-row]').first().locator('td').first().click();
    const box = (await page.locator('[data-ops-drawer]').boundingBox())!;
    expect(Math.round(box.width)).toBe(390);
    await page.keyboard.press('Escape');
  });
});

test('logs: só registros reais (linha do gateway após usar um app) @core', async ({ page }) => {
  await openApp(page, '/apps/ativos/dashboard');
  const api = await apiAs('admin');
  const all = await json(await api.get('/api/admin/logs?app=all&minutes=1440'));
  expect(all.some((l: { source: string }) => l.source === 'simulated' || l.source === 'seed')).toBe(false);
  expect(all.some((l: { user: string }) => /john\.doe|lucas\.ferreira/.test(l.user))).toBe(false);
  expect(all.some((l: { source: string; app: string }) => l.source === 'gateway' && l.app === 'ativos')).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin/logs');
  await expect(page.locator('[data-log-terminal]')).toContainText('[gateway]');
  await page.locator('[data-log-source]').selectOption('gateway');
  await expect(page.locator('[data-log-terminal] .log-line').first()).toContainText('[gateway]');
  await page.locator('[data-log-source]').selectOption('');
  await page.screenshot({ path: path.join(DIST, 'logs-real.png') });
});
